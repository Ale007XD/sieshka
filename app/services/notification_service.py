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

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        # reply_markup added sprint_telegram_bot_entrypoint (2026-08-15) for
        # the /start inline "Open App" web_app button — optional, keyword-
        # only, defaults to None so every existing fire-and-forget call site
        # in notification_tools.py is unaffected.
        payload: dict[str, object] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.BASE_URL}{self._token}/sendMessage",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]


telegram_client = TelegramClient(token=settings.TELEGRAM_BOT_TOKEN)
# sprint_telegram_bot_entrypoint: exported singleton, mirrors max_client
# (app/services/max_client.py) — app/webhooks/telegram.py depends on this
# via a get_telegram_client() indirection, not NotificationService, since
# the webhook needs raw send_message(reply_markup=...) capability that
# NotificationService.send_telegram() deliberately doesn't expose (its
# fire-and-forget contract is plain-text notifications only).


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
