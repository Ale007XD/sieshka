"""app/webhooks/telegram.py — Telegram Bot API webhook: /start entrypoint
for the Mini App (sprint_telegram_bot_entrypoint).

Scope deliberately narrow, mirrors app/webhooks/max.py's own
bot_started/message_created stub note (that file leaves the storefront-open
reply unbuilt entirely — MAX's Mini App is reachable via a static Menu
Button configured once outside this codebase). This module goes one step
further than MAX's stub: a real /start reply with an inline "Open App"
web_app button, since Telegram's persistent Menu Button (BotFather
/setmenubutton, also zero-code) is a nice-to-have here, not a substitute for
an explicit greeting+button on first contact.

Staff actions do NOT go through this webhook. Both staff surfaces —
Mini App webview (app/api/routes/telegram_miniapp.py, sprint_telegram_
miniapp_backend_api) and any future inline-keyboard callback_query dispatch
(MAX's pattern, app/webhooks/max.py) — would share the same
app/services/staff_dispatch.py ACL either way; this sprint only wires the
webview path, so callback_query handling is intentionally NOT implemented
here (not stubbed with dead code either — simply absent, added if/when a
chat-based staff action path is actually requested).

Webhook security: X-Telegram-Bot-Api-Secret-Token header, Telegram's own
mechanism for verifying a webhook call actually came from Telegram (set via
setWebhook's secret_token param) — same role as MAX's X-Max-Bot-Api-Secret
(app/webhooks/max.py) / settings.MAX_WEBHOOK_SECRET, same fail-open-on-
empty-config posture (an unset secret disables the check rather than
rejecting every request, matching MAX's own settings.MAX_WEBHOOK_SECRET
conditional).

Never fails Telegram's delivery retry loop: every code path below returns
200 {"ok": true}, same posture as max_webhook.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.notification_service import TelegramClient, telegram_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_START_GREETING = "Добро пожаловать в Sieshka! Откройте приложение, чтобы сделать заказ."


def get_telegram_client() -> TelegramClient:
    return telegram_client


def _open_app_markup() -> dict[str, object] | None:
    """None when TELEGRAM_MINIAPP_URL isn't configured yet (sprint_telegram_
    miniapp_frontend not built) — degrades to a plain-text greeting rather
    than sending a button pointing nowhere."""
    if not settings.TELEGRAM_MINIAPP_URL:
        return None
    return {
        "inline_keyboard": [
            [{"text": "Открыть приложение", "web_app": {"url": settings.TELEGRAM_MINIAPP_URL}}]
        ]
    }


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    client: TelegramClient = Depends(get_telegram_client),
) -> JSONResponse:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if settings.TELEGRAM_WEBHOOK_SECRET and secret != settings.TELEGRAM_WEBHOOK_SECRET:
        logger.warning("Telegram webhook: invalid webhook secret")
        return JSONResponse({"ok": False}, status_code=403)

    try:
        update: dict[str, object] = await request.json()
    except Exception:
        logger.warning("Telegram webhook: invalid JSON body")
        return JSONResponse({"ok": True})  # never fail Telegram's delivery retry loop

    message = update.get("message")
    if not isinstance(message, dict):
        logger.debug("Telegram webhook: ignored update (no message field)")
        return JSONResponse({"ok": True, "ignored": True})

    text = message.get("text")
    if not isinstance(text, str) or not text.startswith("/start"):
        logger.debug("Telegram webhook: ignored non-/start message")
        return JSONResponse({"ok": True, "ignored": True})

    chat = message.get("chat") or {}
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id is None:
        logger.warning("Telegram webhook: /start message without a resolvable chat id")
        return JSONResponse({"ok": True})

    try:
        await client.send_message(
            str(chat_id), _START_GREETING, reply_markup=_open_app_markup()
        )
    except Exception:
        logger.exception("Telegram webhook: failed to send /start reply to chat_id=%s", chat_id)

    return JSONResponse({"ok": True})
