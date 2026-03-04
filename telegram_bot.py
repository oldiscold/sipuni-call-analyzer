"""
Sipuni Call Analyzer - Telegram Bot

Отправка результатов анализа звонков и уведомлений об ошибках в Telegram.
"""

import logging
import os
import re
from typing import Optional

from dotenv import load_dotenv
from telegram import Bot
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

load_dotenv()

logger = logging.getLogger(__name__)

# Настройки Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Лимит символов в одном сообщении Telegram
MAX_MESSAGE_LENGTH = 4000  # С запасом от 4096

# Инициализация бота с увеличенными таймаутами
if TELEGRAM_BOT_TOKEN:
    request = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0, write_timeout=20.0)
    bot = Bot(token=TELEGRAM_BOT_TOKEN, request=request)
else:
    bot = None


def escape_markdown(text: str) -> str:
    """
    Экранирует специальные символы Markdown.

    Telegram Markdown требует экранирования: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


def format_direction(direction: str) -> str:
    """Форматирует направление звонка."""
    if direction == "incoming":
        return "📥 Входящий"
    elif direction == "outgoing":
        return "📤 Исходящий"
    return f"📞 {direction}"


async def send_message(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    Отправляет сообщение в Telegram.

    Если текст длиннее лимита — разбивает на части.
    """
    if not bot or not TELEGRAM_CHAT_ID:
        logger.error("Telegram не настроен: отсутствует токен или chat_id")
        return False

    try:
        # Разбиваем длинные сообщения
        messages = split_message(text, MAX_MESSAGE_LENGTH)

        for i, msg in enumerate(messages):
            if i > 0:
                # Небольшая пауза между сообщениями
                import asyncio

                await asyncio.sleep(0.5)

            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=parse_mode,
            )

        return True

    except Exception as e:
        logger.error(f"Ошибка отправки в Telegram: {e}")
        # Пробуем отправить без форматирования
        if parse_mode:
            try:
                for msg in split_message(text, MAX_MESSAGE_LENGTH):
                    await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=msg,
                    )
                return True
            except Exception as e2:
                logger.error(f"Ошибка отправки без форматирования: {e2}")
        return False


def split_message(text: str, max_length: int) -> list[str]:
    """
    Разбивает сообщение на части по max_length символов.

    Пытается разбить по переносам строк для читаемости.
    """
    if len(text) <= max_length:
        return [text]

    messages = []
    current = ""

    for line in text.split("\n"):
        if len(current) + len(line) + 1 <= max_length:
            current += line + "\n"
        else:
            if current:
                messages.append(current.rstrip())
            # Если одна строка длиннее лимита — режем по символам
            while len(line) > max_length:
                messages.append(line[:max_length])
                line = line[max_length:]
            current = line + "\n"

    if current.strip():
        messages.append(current.rstrip())

    return messages


async def send_analysis_result(
    call_id: str,
    manager_name: str,
    call_start: Optional[str],
    duration: int,
    direction: str,
    caller_number: str,
    called_number: str,
    analysis: str,
) -> bool:
    """
    Отправляет результат анализа звонка в Telegram.

    Формирует сообщение с шапкой и фидбеком от GPT-4o.
    """
    # Форматируем дату
    call_date = call_start or "Неизвестно"

    # Форматируем направление
    direction_text = format_direction(direction)

    # Формируем сообщение
    message = f"""📊 *Анализ звонка*
👤 Менеджер: {manager_name}
📅 Дата: {call_date}
⏱ Длительность: {duration} сек
{direction_text}: {caller_number} → {called_number}
📋 Метод оценки: CQR (Call Quality Rate)
━━━━━━━━━━━━━━━
{analysis}"""

    logger.info(f"Отправляем анализ звонка {call_id} в Telegram")

    # Пробуем отправить с Markdown
    success = await send_message(message, ParseMode.MARKDOWN)

    if not success:
        # Если не получилось с Markdown — отправляем без форматирования
        plain_message = message.replace("*", "").replace("_", "")
        success = await send_message(plain_message)

    return success


async def send_error_notification(call_id: str, error_message: str) -> bool:
    """
    Отправляет уведомление об ошибке в Telegram.
    """
    message = f"❌ Ошибка обработки звонка {call_id}:\n{error_message}"

    logger.info(f"Отправляем уведомление об ошибке для звонка {call_id}")

    return await send_message(message)


async def send_transcription_error(
    call_id: str,
    manager_name: str,
) -> bool:
    """
    Отправляет уведомление о неудачной транскрибации.
    """
    message = f"⚠️ Не удалось распознать звонок {call_id} от {manager_name}"

    logger.info(f"Отправляем уведомление о неудачной транскрибации для {call_id}")

    return await send_message(message)
