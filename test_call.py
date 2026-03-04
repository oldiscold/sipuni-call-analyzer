"""
Тестовый скрипт — прогоняет конкретную запись звонка через полный пайплайн.

Запуск: py test_call.py
"""

import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from analyzer import process_call

RECORDING_URL = "https://sipuni.com/api/crm/record?id=1772435966.432495&hash=221a7cb67e8de5d743a06041e95f3085&user=089071"


async def main():
    print(f"\n▶ Запускаем тест для:\n  {RECORDING_URL}\n")

    await process_call(
        call_id="1772435966.432495",
        recording_url=RECORDING_URL,
        duration=185,
        caller_number="089071219",
        called_number="77714574010",
        direction="outgoing",
        manager_name="Менеджер (внутр. 219)",
        call_start="2026-03-02T13:19:27",
    )

    print("\n✅ Готово — проверяй Telegram")


asyncio.run(main())
