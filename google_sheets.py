"""
Google Sheets интеграция для записи результатов анализа звонков.

Credentials берутся из переменной окружения GOOGLE_SHEETS_CREDENTIALS_JSON.
Spreadsheet ID из GOOGLE_SHEET_ID.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Конфигурация из env
GOOGLE_SHEETS_CREDENTIALS_JSON = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Звонки")

# Заголовки таблицы (в порядке столбцов)
SHEET_HEADERS = [
    "Дата и время",
    "Менеджер",
    "Внутренний номер",
    "Номер клиента",
    "Направление",
    "Длительность (сек)",
    "CQR Балл",
    "Приветствие",
    "Речь",
    "Инициатива",
    "Проблема",
    "Продукт",
    "Возражение",
    "Дожим",
    "Выгоды",
    "Следующий шаг",
    "Боли клиента",
    "Рекомендация",
    "Ниша клиента",
    "Источник",
]


def _get_sheet():
    """
    Возвращает объект листа gspread.

    Инициализирует клиент из JSON-строки в env-переменной.
    Возвращает None если credentials не настроены.
    """
    if not GOOGLE_SHEETS_CREDENTIALS_JSON:
        logger.warning("GOOGLE_SHEETS_CREDENTIALS_JSON не настроен, пропускаем запись в Sheets")
        return None
    if not GOOGLE_SHEET_ID:
        logger.warning("GOOGLE_SHEET_ID не настроен, пропускаем запись в Sheets")
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        credentials_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

        # Пробуем открыть лист, создаём если не существует
        try:
            worksheet = spreadsheet.worksheet(GOOGLE_SHEET_NAME)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=GOOGLE_SHEET_NAME, rows=1000, cols=len(SHEET_HEADERS))
            logger.info(f"Создан новый лист '{GOOGLE_SHEET_NAME}'")

        return worksheet

    except json.JSONDecodeError as e:
        logger.error(f"Некорректный JSON в GOOGLE_SHEETS_CREDENTIALS_JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None


def _ensure_headers(worksheet) -> None:
    """Проверяет наличие заголовков и создаёт их если лист пустой."""
    try:
        first_row = worksheet.row_values(1)
        if not first_row:
            worksheet.append_row(SHEET_HEADERS, value_input_option="RAW")
            logger.info("Добавлены заголовки таблицы")
    except Exception as e:
        logger.error(f"Ошибка проверки заголовков: {e}")


def _build_row(call_data: dict) -> list[Any]:
    """Формирует строку для вставки в таблицу из словаря call_data."""
    cqr = call_data.get("cqr_scores", {})

    # Дата и время
    call_start_ts = call_data.get("call_start_timestamp")
    if call_start_ts:
        dt = datetime.fromtimestamp(int(call_start_ts))
        date_str = dt.strftime("%d.%m.%Y %H:%M")
    else:
        date_str = call_data.get("call_start", "") or ""

    # Направление
    direction = call_data.get("direction", "incoming")
    direction_ru = "Исходящий" if direction == "outgoing" else "Входящий"

    return [
        date_str,
        call_data.get("manager_name", ""),
        call_data.get("manager_short_num", ""),
        call_data.get("client_number", ""),
        direction_ru,
        call_data.get("duration", 0),
        call_data.get("cqr_total", ""),
        cqr.get("greeting", ""),
        cqr.get("speech", ""),
        cqr.get("initiative", ""),
        cqr.get("problem", ""),
        cqr.get("product", ""),
        cqr.get("objection", ""),
        cqr.get("closing", ""),
        cqr.get("benefits", ""),
        cqr.get("next_step", ""),
        call_data.get("client_pains", ""),
        call_data.get("recommendation", ""),
        call_data.get("client_niche", ""),
        call_data.get("lead_source", ""),
    ]


def _append_row_sync(call_data: dict) -> None:
    """Синхронная запись строки в Google Sheets."""
    worksheet = _get_sheet()
    if worksheet is None:
        return

    _ensure_headers(worksheet)
    row = _build_row(call_data)
    worksheet.append_row(row, value_input_option="USER_ENTERED")
    logger.info(f"Звонок {call_data.get('call_id')} записан в Google Sheets")


async def append_call_to_sheet(call_data: dict) -> None:
    """
    Асинхронная запись результатов звонка в Google Sheets.

    gspread синхронный, оборачиваем в run_in_executor.
    При любой ошибке логирует и не крашит основной поток.
    """
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _append_row_sync, call_data)
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets (звонок {call_data.get('call_id')}): {e}")
