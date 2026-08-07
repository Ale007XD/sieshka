"""app/services/max_client.py — raw MAX Bot API transport client.

sprint_max_client scope: transport primitives only (send/answer/edit) —
no keyboard-building, no OrderEvent/KitchenEvent business logic. That logic
is role- and FSM-specific and belongs to the webhook adapter
(sprint_max_webhook_adapter) and staff notify (sprint_max_staff_notify), both
of which import this client rather than duplicating HTTP calls.

Ported from SieshKa-Site/app/max_notify.py (the working MAX integration
already live at siesh-ka.ru) — that module's send_max_message/
answer_max_callback/edit_max_message functions are pure transport, unrelated
to its own ad-hoc OrderStatus graph, so they port near-verbatim as methods
here. What does NOT port: build_order_status_keyboard() and friends — those
are keyed to the old single-enum OrderStatus model. The new repo has two
separate FSMs (KitchenEvent, OrderEvent); keyboard-building against them is
sprint_max_webhook_adapter's job, not this client's.

TLS / Mintsifry certificate (see settings.MAX_API_BASE_URL comment):
platform-api2.max.ru's certificate chains to the same Russian Trusted Root CA
already installed in this container's combined CA bundle for GigaChat
(Dockerfile, 2026-07-27 fix) and pointed to via the SSL_CERT_FILE env var.
httpx>=0.28 (pinned in pyproject.toml) reads SSL_CERT_FILE automatically for
its default `verify=True` case (confirmed by reading httpx._config.
create_ssl_context source: `if trust_env and os.environ.get("SSL_CERT_FILE")`)
— unlike litellm, which needed an explicit env-var check (Dockerfile comment,
2026-07-27). A plain `httpx.AsyncClient()` here, same as TelegramClient in
notification_service.py, is therefore sufficient — no separate cert bootstrap
step for MAX.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class MaxClient:
    """Raw MAX Bot API client via httpx."""

    def __init__(self, token: str, base_url: str | None = None) -> None:
        self._token = token
        self._base_url = base_url or settings.MAX_API_BASE_URL

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token,
            "Content-Type": "application/json",
        }

    async def send_message(
        self,
        user_id: int,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """POST /messages. Returns message_id (mid) on success, None on failure."""
        if not self.configured:
            logger.warning("MAX_BOT_TOKEN not set, skipping MAX send_message")
            return None

        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/messages",
                    params={"user_id": user_id},
                    json=body,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                # MAX иногда возвращает success=false при наличии inline_keyboard,
                # хотя сообщение доставлено — видно по message.body.mid в ответе
                # (подтверждённое поведение MAX API, см. SieshKa-Site инцидент).
                mid = (data.get("message") or {}).get("body", {}).get("mid")
                if not (data.get("success") or bool(mid)):
                    logger.error(
                        "MAX /messages success=false for user_id=%s: %s", user_id, data
                    )
                    return None
                logger.info("MAX /messages OK for user_id=%s mid=%s", user_id, mid or "?")
                return mid  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as e:
            logger.error(
                "MAX /messages HTTP error for user_id=%s: %s", user_id, e.response.text
            )
        except ValueError as e:
            logger.error("MAX /messages JSON parse error for user_id=%s: %s", user_id, e)
        except httpx.RequestError as e:
            logger.error("MAX /messages request error for user_id=%s: %s", user_id, e)
        return None

    async def edit_message(
        self,
        message_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> bool:
        """PUT /messages — edit an existing MAX message."""
        if not self.configured:
            logger.warning("MAX_BOT_TOKEN not set, skipping MAX edit_message")
            return False

        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.put(
                    f"{self._base_url}/messages",
                    params={"message_id": message_id},
                    json=body,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            success = bool(data.get("success", False))
            if not success:
                logger.error(
                    "MAX PUT /messages success=false for mid=%s: %s", message_id, data
                )
            return success
        except httpx.HTTPStatusError as e:
            logger.error(
                "MAX PUT /messages HTTP error for mid=%s: %s", message_id, e.response.text
            )
        except ValueError as e:
            logger.error(
                "MAX PUT /messages JSON parse error for mid=%s: %s", message_id, e
            )
        except httpx.RequestError as e:
            logger.error("MAX PUT /messages request error for mid=%s: %s", message_id, e)
        return False

    async def answer_callback(
        self,
        callback_id: str,
        notification: str | None = None,
        message_text: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> bool:
        """POST /answers — acknowledge a button callback.

        notification: popup text shown to the user, max 64 chars per MAX API.
        message_text/attachments: if given, updates the message in-place
        (server-side edit-on-answer, distinct from the explicit edit_message()
        PUT above which can be called outside a callback's request/response).
        """
        if not self.configured:
            logger.warning("MAX_BOT_TOKEN not set, skipping MAX answer_callback")
            return False

        body: dict[str, Any] = {}
        if notification:
            body["notification"] = notification
        if message_text is not None or attachments is not None:
            body["message"] = {
                "text": message_text if message_text is not None else "",
                "attachments": attachments or [],
            }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/answers",
                    params={"callback_id": callback_id},
                    json=body,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            success = bool(data.get("success", False))
            if not success:
                logger.error(
                    "MAX /answers success=false for callback_id=%s: %s",
                    callback_id,
                    data,
                )
            return success
        except httpx.HTTPStatusError as e:
            logger.error(
                "MAX /answers HTTP error for callback_id=%s: %s",
                callback_id,
                e.response.text,
            )
        except ValueError as e:
            logger.error(
                "MAX /answers JSON parse error for callback_id=%s: %s", callback_id, e
            )
        except httpx.RequestError as e:
            logger.error(
                "MAX /answers request error for callback_id=%s: %s", callback_id, e
            )
        return False


max_client = MaxClient(token=settings.MAX_BOT_TOKEN)
