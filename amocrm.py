"""
AmoCRM интеграция — запись анализа звонка в примечание сделки клиента.

Поток:
1. Поиск контакта по номеру телефона
2. Получение связанных сделок контакта
3. Запись примечания в первую сделку
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

AMO_SUBDOMAIN = os.getenv("AMO_SUBDOMAIN", "")
AMO_CLIENT_ID = os.getenv("AMO_CLIENT_ID", "")
AMO_CLIENT_SECRET = os.getenv("AMO_CLIENT_SECRET", "")
AMO_REDIRECT_URI = os.getenv("AMO_REDIRECT_URI", "https://example.com")
AMO_ACCESS_TOKEN = os.getenv("AMO_ACCESS_TOKEN", "")
AMO_REFRESH_TOKEN = os.getenv("AMO_REFRESH_TOKEN", "")

TOKEN_FILE = Path(os.getenv("AMO_TOKEN_FILE", "/tmp/amocrm_tokens.json"))


class AmoCRMClient:
    def __init__(self):
        self.subdomain = AMO_SUBDOMAIN
        self.client_id = AMO_CLIENT_ID
        self.client_secret = AMO_CLIENT_SECRET
        self.redirect_uri = AMO_REDIRECT_URI
        self.base_url = f"https://{self.subdomain}.amocrm.ru/api/v4"
        self.access_token = AMO_ACCESS_TOKEN
        self.refresh_token = AMO_REFRESH_TOKEN
        self._load_tokens_from_file()

    def _load_tokens_from_file(self):
        try:
            if TOKEN_FILE.exists():
                data = json.loads(TOKEN_FILE.read_text())
                if data.get("access_token"):
                    self.access_token = data["access_token"]
                if data.get("refresh_token"):
                    self.refresh_token = data["refresh_token"]
        except Exception as e:
            logger.warning(f"AmoCRM: не удалось загрузить токены из файла: {e}")

    def _save_tokens_to_file(self):
        try:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(json.dumps({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
            }))
        except Exception as e:
            logger.warning(f"AmoCRM: не удалось сохранить токены в файл: {e}")

    def _refresh_access_token(self) -> bool:
        if not all([self.subdomain, self.client_id, self.client_secret, self.refresh_token]):
            logger.error("AmoCRM: не хватает данных для обновления токена")
            return False
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"https://{self.subdomain}.amocrm.ru/oauth2/access_token",
                    json={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": self.refresh_token,
                        "redirect_uri": self.redirect_uri,
                    },
                )
                if resp.status_code != 200:
                    logger.error(f"AmoCRM: ошибка обновления токена {resp.status_code}: {resp.text[:300]}")
                    return False
                data = resp.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                self._save_tokens_to_file()
                logger.info("AmoCRM: токен успешно обновлён")
                return True
        except Exception as e:
            logger.error(f"AmoCRM: ошибка при обновлении токена: {e}")
            return False

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        for attempt in range(2):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = getattr(client, method)(url, headers=self._get_headers(), **kwargs)

                if resp.status_code == 401 and attempt == 0:
                    logger.info("AmoCRM: токен истёк, обновляем...")
                    if self._refresh_access_token():
                        continue
                    return None

                if resp.status_code == 204:
                    return {}

                if resp.status_code not in (200, 201):
                    logger.error(f"AmoCRM {method.upper()} {endpoint}: HTTP {resp.status_code} — {resp.text[:300]}")
                    return None

                return resp.json()

            except Exception as e:
                logger.error(f"AmoCRM: ошибка запроса {method.upper()} {endpoint}: {e}")
                return None

        return None

    def find_contact_by_phone(self, phone: str) -> Optional[int]:
        phone_clean = "".join(c for c in phone if c.isdigit())
        if not phone_clean:
            return None

        data = self._request("get", "contacts", params={"query": phone_clean})
        if not data:
            return None

        contacts = data.get("_embedded", {}).get("contacts", [])
        if not contacts:
            logger.info(f"AmoCRM: контакт с номером {phone} не найден")
            return None

        contact_id = contacts[0]["id"]
        logger.info(f"AmoCRM: найден контакт id={contact_id} по номеру {phone}")
        return contact_id

    def get_contact_leads(self, contact_id: int) -> list:
        data = self._request("get", f"contacts/{contact_id}/links", params={"to": "leads"})
        if not data:
            return []

        links = data.get("_embedded", {}).get("links", [])
        lead_ids = [
            link["to_entity_id"]
            for link in links
            if link.get("to_entity_type") == "leads"
        ]
        logger.info(f"AmoCRM: контакт {contact_id} связан со сделками: {lead_ids}")
        return lead_ids

    def add_note_to_lead(self, lead_id: int, text: str) -> bool:
        data = self._request(
            "post",
            "leads/notes",
            json=[{
                "entity_id": lead_id,
                "note_type": "common",
                "params": {"text": text},
            }],
        )
        if data is not None:
            logger.info(f"AmoCRM: примечание добавлено к сделке {lead_id}")
            return True
        return False

    def add_call_analysis_note(
        self,
        client_phone: str,
        client_pains: str,
        recommendation: str,
        manager_name: str = "",
        call_start: str = "",
    ) -> bool:
        if not self.access_token:
            logger.warning("AmoCRM: AMO_ACCESS_TOKEN не настроен, пропускаем")
            return False

        contact_id = self.find_contact_by_phone(client_phone)
        if not contact_id:
            return False

        lead_ids = self.get_contact_leads(contact_id)
        if not lead_ids:
            logger.info(f"AmoCRM: сделок не найдено для контакта {contact_id}")
            return False

        lines = []
        if call_start:
            lines.append(f"📞 Анализ звонка: {call_start}")
        if manager_name:
            lines.append(f"Менеджер: {manager_name}")
        lines.append("")
        lines.append(f"💢 Боли клиента:\n{client_pains}")
        lines.append("")
        lines.append(f"💡 Рекомендация:\n{recommendation}")

        note_text = "\n".join(lines)
        return self.add_note_to_lead(lead_ids[0], note_text)


_client: Optional[AmoCRMClient] = None


def get_amocrm_client() -> Optional[AmoCRMClient]:
    global _client
    if _client is None:
        if AMO_SUBDOMAIN and (AMO_ACCESS_TOKEN or TOKEN_FILE.exists()):
            _client = AmoCRMClient()
        else:
            return None
    return _client


async def add_call_note_to_amocrm(
    client_phone: str,
    client_pains: str,
    recommendation: str,
    manager_name: str = "",
    call_start: str = "",
) -> None:
    client = get_amocrm_client()
    if client is None:
        logger.info("AmoCRM не настроен (AMO_SUBDOMAIN/AMO_ACCESS_TOKEN отсутствуют), пропускаем")
        return

    try:
        await asyncio.to_thread(
            client.add_call_analysis_note,
            client_phone,
            client_pains,
            recommendation,
            manager_name,
            call_start,
        )
    except Exception as e:
        logger.error(f"AmoCRM: необработанная ошибка: {e}")
