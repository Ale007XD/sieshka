"""tests/unit/test_telegram_webhook.py — Telegram bot webhook /start
entrypoint (sprint_telegram_bot_entrypoint).

No DB/Docker required — TelegramClient mocked via dependency_overrides,
mirrors test_max_webhook.py's transport-only split. Scope here is
deliberately narrow (see app/webhooks/telegram.py docstring): /start only,
no callback_query dispatch.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.services.notification_service import TelegramClient
from app.webhooks.telegram import get_telegram_client
from app.webhooks.telegram import router as telegram_router


class _Mocks:
    def __init__(self) -> None:
        self.telegram = AsyncMock(spec=TelegramClient)


@pytest.fixture
def mocks() -> _Mocks:
    return _Mocks()


@pytest.fixture
async def client(mocks: _Mocks):
    app = FastAPI()
    app.include_router(telegram_router)
    app.dependency_overrides[get_telegram_client] = lambda: mocks.telegram
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _update(text: str = "/start", chat_id: int = 555) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False},
            "text": text,
        },
    }


class TestTelegramWebhookSecret:
    async def test_wrong_secret_returns_403(self, client: AsyncClient, mocks: _Mocks) -> None:
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", "expected"):
            resp = await client.post(
                "/webhooks/telegram",
                json=_update(),
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
        assert resp.status_code == 403
        mocks.telegram.send_message.assert_not_awaited()

    async def test_no_secret_configured_skips_check(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", ""):
            resp = await client.post("/webhooks/telegram", json=_update())
        assert resp.status_code == 200
        mocks.telegram.send_message.assert_awaited_once()


class TestTelegramWebhookStart:
    async def test_start_sends_greeting(self, client: AsyncClient, mocks: _Mocks) -> None:
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", ""):
            resp = await client.post("/webhooks/telegram", json=_update(chat_id=555))

        assert resp.status_code == 200
        mocks.telegram.send_message.assert_awaited_once()
        call = mocks.telegram.send_message.call_args
        assert call.args[0] == "555"

    async def test_start_with_miniapp_url_attaches_web_app_button(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        with (
            patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", ""),
            patch.object(settings, "TELEGRAM_MINIAPP_URL", "https://app.example.com"),
        ):
            resp = await client.post("/webhooks/telegram", json=_update())

        assert resp.status_code == 200
        markup = mocks.telegram.send_message.call_args.kwargs["reply_markup"]
        assert markup["inline_keyboard"][0][0]["web_app"]["url"] == "https://app.example.com"

    async def test_start_without_miniapp_url_sends_no_button(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        with (
            patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", ""),
            patch.object(settings, "TELEGRAM_MINIAPP_URL", ""),
        ):
            resp = await client.post("/webhooks/telegram", json=_update())

        assert resp.status_code == 200
        assert mocks.telegram.send_message.call_args.kwargs["reply_markup"] is None

    async def test_non_start_message_ignored(self, client: AsyncClient, mocks: _Mocks) -> None:
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", ""):
            resp = await client.post(
                "/webhooks/telegram", json=_update(text="hello there")
            )
        assert resp.status_code == 200
        mocks.telegram.send_message.assert_not_awaited()

    async def test_non_message_update_ignored(self, client: AsyncClient, mocks: _Mocks) -> None:
        with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", ""):
            resp = await client.post(
                "/webhooks/telegram", json={"update_id": 2, "edited_message": {}}
            )
        assert resp.status_code == 200
        mocks.telegram.send_message.assert_not_awaited()

    async def test_invalid_json_body_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/webhooks/telegram",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
