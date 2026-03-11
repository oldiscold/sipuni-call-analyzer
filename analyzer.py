"""
Sipuni Call Analyzer - Транскрипция и анализ звонков

Транскрибация через Groq Whisper (large-v3), анализ через OpenAI GPT-4o.
"""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from groq import Groq
from openai import AsyncOpenAI

from config import get_manager_name
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

# Системный промпт для анализа звонков (методика CQR, 9 критериев)
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
2. РЕЧЬ: 1=чистая речь без паразитов и жаргона, 0.5=до 2 слов-паразитов, 0=жаргон/мат/5+ паразитов.
3. ИНИЦИАТИВА: 1=менеджер ведёт беседу и задаёт направление, 0.5=формат вопрос-ответ без ведения, 0=пассивен, клиент ведёт разговор.
4. ПРОБЛЕМА: 1=полностью выяснил потребности клиента, 0.5=частично выяснил, 0=не выяснил.
5. ПРОДУКТ: 1=связал потребность клиента с продуктом + назвал 3+ преимущества, 0.5=назвал преимущества без привязки к потребности, 0=не презентовал продукт.
6. ВОЗРАЖЕНИЕ: 1=вскрыл возражение и качественно отработал, 0.5=вскрыл но не отработал, 0=не вскрыл или проигнорировал. Если возражений объективно не было — ставь 1.
7. ДОЖИМ: 1=использовал скидки/бонусы/закрывающие вопросы для продвижения к сделке, 0.5=упомянул вскользь, 0=не пытался закрыть.
8. ВЫГОДЫ: 1=озвучил 3+ конкретные выгоды для клиента (не свойства продукта, а именно выгоды — что клиент получит), 0.5=1-2 выгоды, 0=не озвучил выгод.
9. СЛЕДУЮЩИЙ ШАГ: 1=чётко договорился о следующем действии (точное время перезвона, перевод в WhatsApp/Telegram для продолжения, назначена встреча, отправка КП с датой обратной связи), 0.5=неточные сроки типа "я вам перезвоню"/"напишу на днях", 0=не обозначил следующий шаг.

ДОПОЛНИТЕЛЬНЫЙ АНАЛИЗ:

БОЛИ КЛИЕНТА (БИЗНЕС): Выдели ключевые БИЗНЕС-ПРОБЛЕМЫ клиента — что мешает его бизнесу расти, что не работает, от чего он страдает как предприниматель/руководитель. Формулируй конкретно с привязкой к разговору, 1-2 предложения на каждую боль. Если клиент не озвучил бизнес-болей — напиши "Не выявлены (менеджер не выяснил)".

ЖЕЛАНИЯ КЛИЕНТА: Что клиент хочет получить для своего бизнеса — цели, планы, ожидания, к чему стремится (рост продаж, масштабирование, автоматизация, выход на новый рынок и т.д.). Формулируй конкретно из контекста разговора, 1-2 предложения на каждый пункт. Если не озвучил — напиши "Не выявлены (менеджер не выяснил)".

ВОЗРАЖЕНИЯ КЛИЕНТА: Перечисли все возражения, сомнения и отговорки клиента из разговора. Для каждого укажи: что сказал клиент → как отреагировал менеджер. Если возражений не было — напиши "Возражений не было".

НИША КЛИЕНТА: Если в разговоре упоминается сфера деятельности, бизнес, профессия или компания клиента — укажи. Если не упоминается — напиши "Не определена".

ИСТОЧНИК ОБРАЩЕНИЯ: Если клиент упоминает, откуда узнал о нас (реклама, рекомендация, сайт, Instagram, 2GIS и т.д.) — укажи. Если не упоминается — напиши "Не определён".

ФОРМАТ ОТВЕТА (строго соблюдай, без вступлений и пояснений к пунктам):

📞 Приветствие: [балл]
🗣 Речь: [балл]
💪 Инициатива: [балл]
🔍 Проблема: [балл]
📦 Продукт: [балл]
🛡 Возражение: [балл]
🎯 Дожим: [балл]
✨ Выгоды: [балл]
👉 Следующий шаг: [балл]

[БОЛИ_БИЗНЕС]
- [боль 1 — конкретная бизнес-проблема, 1-2 предложения]
- [боль 2]
[/БОЛИ_БИЗНЕС]

[ЖЕЛАНИЯ]
- [желание 1 — конкретная бизнес-цель, 1-2 предложения]
- [желание 2]
[/ЖЕЛАНИЯ]

[ВОЗРАЖЕНИЯ]
- [возражение 1]: клиент сказал "..." → менеджер [отработал/не отработал/проигнорировал]
- [возражение 2]
[/ВОЗРАЖЕНИЯ]

[НИША] [ниша или "Не определена"] [/НИША]
[ИСТОЧНИК] [откуда узнал или "Не определён"] [/ИСТОЧНИК]

📊 CQR: [сумма]/9

[КЛЮЧЕВОЙ_МОМЕНТ] [развёрнуто — 3-5 предложений. Опиши самый важный момент звонка: что именно произошло, как отреагировал клиент, почему это критично для сделки] [/КЛЮЧЕВОЙ_МОМЕНТ]

[РЕКОМЕНДАЦИЯ] [развёрнуто — 3-5 предложений. Конкретные действия для менеджера: что делать в следующем контакте, как исправить ошибки этого звонка, какие техники применить] [/РЕКОМЕНДАЦИЯ]

Только этот формат. Без вступлений и пояснений к пунктам.
ВАЖНО: Строго соблюдай порядок блоков. Теги [БОЛИ_БИЗНЕС], [ЖЕЛАНИЯ], [ВОЗРАЖЕНИЯ], [НИША], [ИСТОЧНИК], [КЛЮЧЕВОЙ_МОМЕНТ], [РЕКОМЕНДАЦИЯ] обязательны."""


def parse_cqr_result(analysis_text: str) -> dict:
    """
    Парсит текст ответа LLM и извлекает структурированные данные CQR.

    Возвращает dict с ключами:
    - cqr_scores: dict с баллами по каждому критерию
    - cqr_total: общий балл (float)
    - client_pains: текст болей клиента
    - recommendation: текст рекомендации
    - raw_text: исходный текст анализа
    """
    result = {
        "cqr_scores": {
            "greeting": "",
            "speech": "",
            "initiative": "",
            "problem": "",
            "product": "",
            "objection": "",
            "closing": "",
            "benefits": "",
            "next_step": "",
        },
        "cqr_total": "",
        "client_pains": "",
        "client_desires": "",
        "client_objections": "",
        "client_niche": "",
        "lead_source": "",
        "key_moment": "",
        "recommendation": "",
        "raw_text": analysis_text,
    }

    if not analysis_text or analysis_text.startswith("⚠️"):
        return result

    # Паттерны для извлечения баллов (учитываем эмодзи и вариации текста)
    score_patterns = [
        ("greeting",   r"Приветствие:\s*([\d.]+)"),
        ("speech",     r"Речь:\s*([\d.]+)"),
        ("initiative", r"Инициатива:\s*([\d.]+)"),
        ("problem",    r"Проблема:\s*([\d.]+)"),
        ("product",    r"Продукт:\s*([\d.]+)"),
        ("objection",  r"Возражение:\s*([\d.]+)"),
        ("closing",    r"Дожим:\s*([\d.]+)"),
        ("benefits",   r"Выгоды:\s*([\d.]+)"),
        ("next_step",  r"Следующий шаг:\s*([\d.]+)"),
    ]

    for key, pattern in score_patterns:
        match = re.search(pattern, analysis_text)
        if match:
            try:
                result["cqr_scores"][key] = float(match.group(1))
            except ValueError:
                pass

    # Общий балл: "CQR: X/9" или "CQR: X"
    total_match = re.search(r"CQR:\s*([\d.]+)(?:/9)?", analysis_text)
    if total_match:
        try:
            result["cqr_total"] = float(total_match.group(1))
        except ValueError:
            pass

    def _parse_tag(tag: str) -> str:
        pattern = rf"\[{tag}\]\s*(.*?)\s*\[/{tag}\]"
        match = re.search(pattern, analysis_text, re.DOTALL)
        if not match:
            return ""
        content = match.group(1).strip()
        lines = content.splitlines()
        bullet_lines = [line.lstrip("-•").strip() for line in lines if line.strip().startswith(("-", "•"))]
        if bullet_lines:
            return "; ".join(bullet_lines)
        return content

    result["client_pains"]      = _parse_tag("БОЛИ_БИЗНЕС")
    result["client_desires"]    = _parse_tag("ЖЕЛАНИЯ")
    result["client_objections"] = _parse_tag("ВОЗРАЖЕНИЯ")
    result["client_niche"]      = _parse_tag("НИША")
    result["lead_source"]       = _parse_tag("ИСТОЧНИК")
    result["key_moment"]        = _parse_tag("КЛЮЧЕВОЙ_МОМЕНТ")
    result["recommendation"]    = _parse_tag("РЕКОМЕНДАЦИЯ")

    return result


async def process_call(
    call_id: str,
    recording_url: str,
    duration: int,
    caller_number: str,
    called_number: str,
    direction: str,
    manager_name: str,
    call_start: Optional[str],
    # Дополнительные поля для Google Sheets
    manager_short_num: Optional[str] = None,
    call_start_timestamp: Optional[int] = None,
) -> None:
    """
    Основной пайплайн обработки звонка.

    1. Скачивание аудио
    2. Транскрибация через Groq Whisper
    3. Анализ через GPT-4o
    4. Отправка результата в Telegram
    5. Запись в Google Sheets
    """
    from google_sheets import append_call_to_sheet

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

        # 3. Анализируем через GPT-4o, получаем структурированный результат
        analysis_result = await analyze_call(
            call_id=call_id,
            transcript=transcript,
            manager_name=manager_name,
            direction=direction,
            duration=duration,
        )

        analysis_text = analysis_result["raw_text"]

        # 4. Отправляем результат в Telegram
        await send_analysis_result(
            call_id=call_id,
            manager_name=manager_name,
            call_start=call_start,
            duration=duration,
            direction=direction,
            caller_number=caller_number,
            called_number=called_number,
            analysis=analysis_text,
            client_niche=analysis_result["client_niche"],
            lead_source=analysis_result["lead_source"],
        )

        # 5. Записываем в Google Sheets
        # Определяем номер клиента (кто не менеджер)
        if direction == "outgoing":
            client_number = called_number
        else:
            client_number = caller_number

        sheet_data = {
            "call_id": call_id,
            "call_start_timestamp": call_start_timestamp,
            "call_start": call_start,
            "manager_name": manager_name,
            "manager_short_num": manager_short_num or "",
            "client_number": client_number,
            "direction": direction,
            "duration": duration,
            "cqr_total": analysis_result["cqr_total"],
            "cqr_scores": analysis_result["cqr_scores"],
            "client_pains": analysis_result["client_pains"],
            "client_desires": analysis_result["client_desires"],
            "client_objections": analysis_result["client_objections"],
            "client_niche": analysis_result["client_niche"],
            "lead_source": analysis_result["lead_source"],
            "recommendation": analysis_result["recommendation"],
        }

        await append_call_to_sheet(sheet_data)

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
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = TEMP_DIR / f"{call_id}.mp3"

    headers = {}
    if SIPUNI_API_KEY:
        headers["Authorization"] = f"Bearer {SIPUNI_API_KEY}"

    max_retries = 2
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(recording_url, headers=headers)
                logger.info(
                    f"Скачивание аудио: HTTP {response.status_code}, "
                    f"Content-Type: {response.headers.get('content-type', '?')}, "
                    f"размер: {len(response.content)} байт"
                )
                if response.status_code != 200:
                    body_preview = response.text[:200]
                    raise RuntimeError(
                        f"HTTP {response.status_code}: {body_preview}"
                    )

                if len(response.content) < 1000:
                    raise RuntimeError(
                        f"Файл слишком маленький ({len(response.content)} байт) — "
                        f"вероятно ошибка авторизации. Ответ: {response.text[:200]}"
                    )

                with open(audio_path, "wb") as f:
                    f.write(response.content)

                return audio_path

        except Exception as e:
            logger.warning(f"Попытка {attempt + 1}/{max_retries}: {repr(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                raise RuntimeError(f"Не удалось скачать аудио: {repr(e)}")


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
) -> dict:
    """
    Анализирует транскрипт звонка через OpenAI GPT-4o.

    Возвращает dict с raw_text и структурированными CQR-данными.
    Retry: 3 попытки с exponential backoff (1с, 2с, 4с).
    """
    fallback_text = f"⚠️ Анализ недоступен. Сырой транскрипт звонка {call_id}:\n\n{transcript}"

    if not openai_client:
        logger.warning("OpenAI API не настроен, возвращаем сырой транскрипт")
        return parse_cqr_result(fallback_text)

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
                max_tokens=3000,
            )
            analysis_text = response.choices[0].message.content or "Анализ не получен"
            logger.info(f"=== RAW LLM RESPONSE ===\n{analysis_text}\n=== END RAW RESPONSE ===")
            return parse_cqr_result(analysis_text)

        except Exception as e:
            logger.warning(
                f"Попытка {attempt + 1}/{max_retries} анализа не удалась: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delays[attempt])
            else:
                logger.error(f"GPT-4o недоступен после {max_retries} попыток")
                return parse_cqr_result(fallback_text)
