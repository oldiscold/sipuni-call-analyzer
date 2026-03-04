"""
Sipuni Call Analyzer - FastAPI Application

Принимает вебхуки от Сипуни (GET с query-параметрами), запускает анализ звонков в фоне.
Транскрибация через Groq Whisper, анализ через Groq Llama 3.3 70B, фидбек в Telegram.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from urllib.parse import unquote

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from pydantic import BaseModel, ConfigDict, field_validator

from analyzer import process_call
from telegram_bot import send_error_notification

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class SipuniWebhook(BaseModel):
    """
    Модель данных вебхука от Сипуни.

    Сипуни отправляет GET-запрос с query-параметрами.
    """

    model_config = ConfigDict(extra="ignore")

    # Основные поля
    call_id: str
    event: Optional[str] = None
    status: str = "unknown"

    # Номера телефонов
    src_num: Optional[str] = None  # Номер звонящего
    dst_num: Optional[str] = None  # Номер назначения
    short_src_num: Optional[str] = None  # Короткий номер звонящего
    short_dst_num: Optional[str] = None  # Короткий номер назначения
    src_type: Optional[str] = None  # Тип источника (1=внутренний, 2=внешний)
    dst_type: Optional[str] = None  # Тип назначения

    # Временные метки (unix timestamp)
    timestamp: Optional[int] = None  # Время окончания звонка
    call_start_timestamp: Optional[int] = None  # Время начала звонка
    call_answer_timestamp: Optional[int] = None  # Время ответа на звонок

    # Ссылка на запись
    call_record_link: Optional[str] = None

    # Дополнительные поля
    channel: Optional[str] = None
    treeName: Optional[str] = None
    treeNumber: Optional[str] = None
    user_id: Optional[str] = None
    last_called: Optional[str] = None
    transfer_from: Optional[str] = None
    pbxdstnum: Optional[str] = None

    @field_validator("call_record_link", mode="before")
    @classmethod
    def decode_url(cls, v):
        """Декодирует URL-encoded ссылку на запись."""
        if v:
            return unquote(v)
        return v

    @property
    def duration(self) -> int:
        """Вычисляет длительность разговора в секундах."""
        if self.timestamp and self.call_answer_timestamp:
            return self.timestamp - self.call_answer_timestamp
        return 0

    @property
    def direction(self) -> str:
        """Определяет направление звонка по treeName."""
        if self.treeName and "Исходящая" in self.treeName:
            return "outgoing"
        return "incoming"

    @property
    def caller_number(self) -> str:
        """Номер звонящего."""
        return self.src_num or self.short_src_num or "Неизвестный"

    @property
    def called_number(self) -> str:
        """Номер на который звонили."""
        return self.dst_num or self.short_dst_num or "Неизвестный"

    @property
    def manager_name(self) -> str:
        """Имя менеджера: short_src_num для исходящих, last_called для входящих."""
        if self.direction == "outgoing":
            num = self.short_src_num or self.user_id or "?"
        else:
            num = self.last_called or self.short_dst_num or self.user_id or "?"
        return f"Менеджер (внутр. {num})"

    @property
    def call_start(self) -> Optional[str]:
        """Время начала звонка в ISO формате."""
        if self.call_start_timestamp:
            return datetime.fromtimestamp(self.call_start_timestamp).isoformat()
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle события приложения."""
    logger.info("Sipuni Call Analyzer запущен")
    yield
    logger.info("Sipuni Call Analyzer остановлен")


app = FastAPI(
    title="Sipuni Call Analyzer",
    description="Анализ звонков менеджеров по продажам",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health-check эндпоинт для мониторинга."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/webhook")
async def receive_webhook_get(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Приём вебхука от Сипуни (GET-запрос с query-параметрами).

    Возвращает 200 OK сразу, обработка запускается в фоне.
    """
    # Получаем все query-параметры
    params = dict(request.query_params)

    # Логируем сырые данные
    logger.info(f"RAW WEBHOOK: {params}")

    try:
        # Конвертируем timestamp поля в int
        for field in ["timestamp", "call_start_timestamp", "call_answer_timestamp"]:
            if field in params and params[field]:
                try:
                    params[field] = int(params[field])
                except ValueError:
                    params[field] = None

        webhook_data = SipuniWebhook(**params)
        logger.info(f"Получен вебхук: call_id={webhook_data.call_id}, status={webhook_data.status}")

    except Exception as e:
        logger.error(f"Ошибка парсинга вебхука: {e}")
        return {"status": "error", "message": "Invalid webhook format"}

    # Проверяем условия для обработки
    if webhook_data.status != "ANSWER":
        logger.info(
            f"Звонок {webhook_data.call_id} не отвечен "
            f"(status={webhook_data.status}), пропускаем"
        )
        return {"status": "skipped", "reason": "call not answered"}

    duration = webhook_data.duration
    if duration < 60:
        logger.info(
            f"Звонок {webhook_data.call_id} слишком короткий "
            f"({duration} сек), пропускаем"
        )
        return {"status": "skipped", "reason": "call too short"}

    if not webhook_data.call_record_link:
        logger.info(f"Звонок {webhook_data.call_id} без записи, пропускаем")
        return {"status": "skipped", "reason": "no recording"}

    # Запускаем обработку в фоне
    background_tasks.add_task(
        process_call_safe,
        webhook_data,
    )

    logger.info(f"Звонок {webhook_data.call_id} поставлен в очередь на обработку")
    return {"status": "queued", "call_id": webhook_data.call_id}


@app.post("/webhook")
async def receive_webhook_post(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Fallback для POST-запросов (на случай если Сипуни изменит формат).
    """
    try:
        body = await request.json()
        logger.info(f"RAW WEBHOOK (POST): {body}")

        # Конвертируем timestamp поля
        for field in ["timestamp", "call_start_timestamp", "call_answer_timestamp"]:
            if field in body and body[field]:
                try:
                    body[field] = int(body[field])
                except (ValueError, TypeError):
                    body[field] = None

        webhook_data = SipuniWebhook(**body)

    except Exception as e:
        logger.error(f"Ошибка парсинга POST вебхука: {e}")
        return {"status": "error", "message": "Invalid webhook format"}

    # Та же логика что и для GET
    if webhook_data.status != "ANSWER":
        logger.info(f"Звонок {webhook_data.call_id} не отвечен, пропускаем")
        return {"status": "skipped", "reason": "call not answered"}

    duration = webhook_data.duration
    if duration < 60:
        logger.info(f"Звонок {webhook_data.call_id} слишком короткий ({duration} сек), пропускаем")
        return {"status": "skipped", "reason": "call too short"}

    if not webhook_data.call_record_link:
        logger.info(f"Звонок {webhook_data.call_id} без записи, пропускаем")
        return {"status": "skipped", "reason": "no recording"}

    background_tasks.add_task(process_call_safe, webhook_data)

    logger.info(f"Звонок {webhook_data.call_id} поставлен в очередь на обработку")
    return {"status": "queued", "call_id": webhook_data.call_id}


async def process_call_safe(webhook_data: SipuniWebhook):
    """
    Безопасная обёртка для обработки звонка.

    Ловит все исключения и отправляет уведомление об ошибке в Telegram.
    """
    try:
        await process_call(
            call_id=webhook_data.call_id,
            recording_url=webhook_data.call_record_link,
            duration=webhook_data.duration,
            caller_number=webhook_data.caller_number,
            called_number=webhook_data.called_number,
            direction=webhook_data.direction,
            manager_name=webhook_data.manager_name,
            call_start=webhook_data.call_start,
        )
    except Exception as e:
        logger.exception(f"Ошибка обработки звонка {webhook_data.call_id}")
        await send_error_notification(
            call_id=webhook_data.call_id,
            error_message=str(e),
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
