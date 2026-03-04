"""
Sipuni Call Analyzer - Транскрипция и анализ звонков

Транскрибация через Groq Whisper (large-v3), анализ через Groq Llama 3.3 70B (бесплатно).
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from groq import Groq
from openai import AsyncOpenAI

from telegram_bot import send_analysis_result, send_error_notification

load_dotenv()

logger = logging.getLogger(__name__)

# API клиенты
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SIPUNI_API_KEY = os.getenv("SIPUNI_API_KEY", "")

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Директория для временных файлов
TEMP_DIR = Path("/tmp/calls")

# Максимальный размер файла для Groq (25 MB)
MAX_FILE_SIZE = 25 * 1024 * 1024

# Системный промпт для анализа звонков (методика CQR)
ANALYSIS_SYSTEM_PROMPT = """Ты — эксперт по контролю качества звонков отдела продаж. Проанализируй транскрипт разговора между Менеджером и Собеседником.

ТРАНСКРИПТ:
{transcript}

ИНФОРМАЦИЯ О ЗВОНКЕ:
- Менеджер: {manager_name}
- Направление: {direction}
- Длительность: {duration} сек

Оцени звонок по методике Call Quality Rate (CQR). Каждый критерий оценивается: 1 / 0.5 / 0 баллов.

КРИТЕРИИ:
1. ПРИВЕТСТВИЕ: 1=назвал себя и компанию, 0.5=только одно, 0=не представился. Если входящий и клиент сразу к сути — ставь 1.
2. РЕЧЬ: 1=чистая речь, 0.5=до 2 слов-паразитов, 0=жаргон/мат/5+ паразитов.
3. ИНИЦИАТИВА: 1=ведёт беседу, 0.5=вопрос-ответ, 0=пассивен.
4. ПРОБЛЕМА: 1=полностью выяснил потребности, 0.5=частично, 0=не выяснил.
5. ПРОДУКТ: 1=потребность+3 преимущества, 0.5=преимущества без потребности, 0=ничего.
6. СОВЕТ: 1=экспертный с обоснованием, 0.5=без обоснования, 0=не дан.
7. ВОЗРАЖЕНИЕ: 1=вскрыл и отработал, 0.5=вскрыл не отработал, 0=не вскрыл. Если возражений не было — 1.
8. ДОЖИМ: 1=скидки/бонусы/закрывающие вопросы, 0.5=вскользь, 0=не пытался.
9. ВЫГОДЫ: 1=3+ выгоды для клиента, 0.5=1-2 выгоды, 0=нет. Выгода ≠ свойство.
10. СЛЕДУЮЩИЙ ШАГ: 1=точное время перезвона, 0.5=неточные сроки, 0=не обозначил.

БОЛИ КЛИЕНТА: Выдели 2-3 ключевые проблемы, страхи или потребности клиента, которые он озвучил или которые подразумеваются из контекста разговора. Формулируй кратко — 1 предложение на каждую боль.

ФОРМАТ ОТВЕТА (строго):

📞 Приветствие: [балл]
🗣 Речь: [балл]
💪 Инициатива: [балл]
🔍 Проблема: [балл]
📦 Продукт: [балл]
💡 Совет: [балл]
🛡 Возражение: [балл]
🎯 Дожим: [балл]
✨ Выгоды: [балл]
👉 Следующий шаг: [балл]

🔥 Боли клиента:
- [боль 1]
- [боль 2]
- [боль 3 если есть]

📊 CQR: [сумма]/10

🔑 Ключевой момент: [1-2 предложения]
💡 Рекомендация: [1-2 предложения]

Только этот формат. Без вступлений и пояснений к пунктам."""


async def process_call(
    call_id: str,
    recording_url: str,
    duration: int,
    caller_number: str,
    called_number: str,
    direction: str,
    manager_name: str,
    call_start: Optional[str],
) -> None:
    """
    Основной пайплайн обработки звонка.

    1. Скачивание аудио
    2. Транскрибация через Groq Whisper
    3. Анализ через GPT-4o
    4. Отправка результата в Telegram
    """
    audio_path = None

    try:
        logger.info(f"Начинаем обработку звонка {call_id}")

        # 1. Скачиваем аудио
        audio_path = await download_audio(call_id, recording_url)
        logger.info(f"Аудио скачано: {audio_path}")

        # Проверяем размер файла
        file_size = audio_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            error_msg = f"Файл слишком большой: {file_size / 1024 / 1024:.1f} MB (лимит 25 MB)"
            logger.error(error_msg)
            await send_error_notification(call_id, error_msg)
            return

        # 2. Транскрибируем через Groq Whisper
        transcript = await transcribe_audio(call_id, audio_path, manager_name)
        if not transcript:
            return  # Ошибка уже отправлена в Telegram

        logger.info(f"Транскрипция готова: {len(transcript)} символов")

        # 3. Анализируем через GPT-4o
        analysis = await analyze_call(
            call_id=call_id,
            transcript=transcript,
            manager_name=manager_name,
            direction=direction,
            duration=duration,
        )

        # 4. Отправляем результат в Telegram
        await send_analysis_result(
            call_id=call_id,
            manager_name=manager_name,
            call_start=call_start,
            duration=duration,
            direction=direction,
            caller_number=caller_number,
            called_number=called_number,
            analysis=analysis,
        )

        logger.info(f"Звонок {call_id} успешно обработан")

    finally:
        # Всегда удаляем аудиофайл
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
                logger.info(f"Аудиофайл удалён: {audio_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления аудиофайла: {e}")


async def download_audio(call_id: str, recording_url: str) -> Path:
    """
    Скачивает аудиофайл по URL.

    Retry: 2 попытки с паузой 2 секунды.
    """
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = TEMP_DIR / f"{call_id}.mp3"

    headers = {}
    if SIPUNI_API_KEY:
        headers["Authorization"] = f"Bearer {SIPUNI_API_KEY}"

    max_retries = 2
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(recording_url, headers=headers)
                response.raise_for_status()

                with open(audio_path, "wb") as f:
                    f.write(response.content)

                return audio_path

        except Exception as e:
            logger.warning(
                f"Попытка {attempt + 1}/{max_retries} скачивания не удалась: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                raise RuntimeError(f"Не удалось скачать аудио: {e}")


async def transcribe_audio(
    call_id: str,
    audio_path: Path,
    manager_name: str,
) -> Optional[str]:
    """
    Транскрибирует аудио через Groq Whisper large-v3.

    Retry: 3 попытки с exponential backoff (1с, 2с, 4с).
    """
    if not groq_client:
        error_msg = "Groq API не настроен"
        logger.error(error_msg)
        await send_error_notification(call_id, error_msg)
        return None

    max_retries = 3
    delays = [1, 2, 4]

    for attempt in range(max_retries):
        try:
            # Groq SDK синхронный, оборачиваем в asyncio.to_thread
            def transcribe_sync():
                with open(audio_path, "rb") as audio_file:
                    return groq_client.audio.transcriptions.create(
                        model="whisper-large-v3",
                        file=audio_file,
                        language="ru",
                        response_format="text",
                    )

            transcript = await asyncio.to_thread(transcribe_sync)
            return transcript

        except Exception as e:
            logger.warning(
                f"Попытка {attempt + 1}/{max_retries} транскрибации не удалась: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delays[attempt])
            else:
                error_msg = f"Не удалось распознать звонок после {max_retries} попыток"
                logger.error(error_msg)
                await send_error_notification(
                    call_id,
                    f"⚠️ Не удалось распознать звонок {call_id} от {manager_name}",
                )
                return None


async def analyze_call(
    call_id: str,
    transcript: str,
    manager_name: str,
    direction: str,
    duration: int,
) -> str:
    """
    Анализирует транскрипт звонка через OpenAI GPT-4o.

    Retry: 3 попытки с exponential backoff (1с, 2с, 4с).
    Если GPT-4o недоступен — возвращает сырой транскрипт.
    """
    if not openai_client:
        logger.warning("OpenAI API не настроен, возвращаем сырой транскрипт")
        return f"⚠️ Анализ недоступен. Сырой транскрипт звонка {call_id}:\n\n{transcript}"

    # Формируем промпт
    prompt = ANALYSIS_SYSTEM_PROMPT.format(
        transcript=transcript,
        manager_name=manager_name,
        direction=direction,
        duration=duration,
    )

    max_retries = 3
    delays = [1, 2, 4]

    for attempt in range(max_retries):
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "Ты — эксперт по анализу звонков отдела продаж.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            analysis = response.choices[0].message.content
            return analysis or "Анализ не получен"

        except Exception as e:
            logger.warning(
                f"Попытка {attempt + 1}/{max_retries} анализа не удалась: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delays[attempt])
            else:
                logger.error(f"GPT-4o недоступен после {max_retries} попыток")
                return f"⚠️ Анализ недоступен. Сырой транскрипт звонка {call_id}:\n\n{transcript}"
