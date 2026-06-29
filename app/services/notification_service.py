"""app/services/notification_service.py — Telegram + SMS stub notifications."""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class TelegramClient:
    """Raw Telegram Bot API client via httpx."""

    BASE_URL = "https://api.telegram.org/bot"

    def __init__(self, token: str) -> None:
        self._token = token

    async def send_message(self, chat_id: str, text: str) -> dict[str, object]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE_URL}{self._token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]


class NotificationService:
    """Fire-and-forget notification dispatcher.

    NOT inside PG transactions — called after commit or in a separate task.
    """

    def __init__(self, telegram: TelegramClient | None = None) -> None:
        self._telegram = telegram or TelegramClient(token=settings.TELEGRAM_BOT_TOKEN)

    async def send_telegram(self, chat_id: str, message: str) -> None:
        """Fire-and-forget Telegram message."""
        if not self._telegram._token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — skipping Telegram notification")
            return
        try:
            await self._telegram.send_message(chat_id, message)
            logger.info("Telegram notification sent to %s", chat_id)
        except Exception:
            logger.exception("Failed to send Telegram notification to %s", chat_id)

    async def send_sms(self, phone: str, message: str) -> None:
        """Stub: log SMS instead of sending."""
        logger.info("SMS stub — to=%s message=%s", phone, message)


notification_service = NotificationService()
