"""tests/unit/test_notification_service.py — mocked Telegram client + tools."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.notification_service import NotificationService, TelegramClient
from app.tools import notification_tools


class TestTelegramClient:
    async def test_send_message_success(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        client = TelegramClient(token="test-token")

        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)):
            result = await client.send_message(chat_id="123", text="Hello")

        assert result["ok"] is True

    async def test_send_message_api_error(self) -> None:
        client = TelegramClient(token="test-token")

        with patch.object(
            httpx.AsyncClient,
            "post",
            AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "error", request=MagicMock(), response=MagicMock(status_code=401)
                )
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await client.send_message(chat_id="123", text="Hello")


class TestNotificationService:
    async def test_send_telegram_success(self) -> None:
        mock_telegram = MagicMock(spec=TelegramClient)
        mock_telegram._token = "test-token"
        mock_telegram.send_message = AsyncMock(return_value={"ok": True})

        svc = NotificationService(telegram=mock_telegram)
        await svc.send_telegram(chat_id="123", message="Hello")

        mock_telegram.send_message.assert_awaited_once_with("123", "Hello")

    async def test_send_telegram_no_token(self) -> None:
        mock_telegram = MagicMock(spec=TelegramClient)
        mock_telegram._token = ""
        mock_telegram.send_message = AsyncMock()

        svc = NotificationService(telegram=mock_telegram)
        await svc.send_telegram(chat_id="123", message="Hello")

        mock_telegram.send_message.assert_not_awaited()

    async def test_send_telegram_error_handled(self) -> None:
        mock_telegram = MagicMock(spec=TelegramClient)
        mock_telegram._token = "test-token"
        mock_telegram.send_message = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock(status_code=400)
            )
        )

        svc = NotificationService(telegram=mock_telegram)
        await svc.send_telegram(chat_id="123", message="Hello")

        mock_telegram.send_message.assert_awaited_once()

    async def test_send_sms_stub(self) -> None:
        svc = NotificationService()
        await svc.send_sms(phone="+79001234567", message="Test SMS")


class TestNotificationTools:
    async def test_notify_order_confirmed(self) -> None:
        with patch.object(notification_tools, "notification_service") as mock_ns:
            mock_ns.send_telegram = AsyncMock()
            result = await notification_tools.notify_order_confirmed(
                order_id="order-1", chat_id="123"
            )

        assert result == "NOTIFIED"
        mock_ns.send_telegram.assert_awaited_once()

    async def test_notify_payment_received(self) -> None:
        with patch.object(notification_tools, "notification_service") as mock_ns:
            mock_ns.send_telegram = AsyncMock()
            result = await notification_tools.notify_payment_received(
                order_id="order-1", chat_id="123"
            )

        assert result == "NOTIFIED"
        mock_ns.send_telegram.assert_awaited_once()

    async def test_notify_order_cooking(self) -> None:
        with patch.object(notification_tools, "notification_service") as mock_ns:
            mock_ns.send_telegram = AsyncMock()
            result = await notification_tools.notify_order_cooking(order_id="order-1")

        assert result == "NOTIFIED"

    async def test_notify_order_delivered(self) -> None:
        with patch.object(notification_tools, "notification_service") as mock_ns:
            mock_ns.send_telegram = AsyncMock()
            result = await notification_tools.notify_order_delivered(order_id="order-1")

        assert result == "NOTIFIED"

    async def test_notify_order_failed(self) -> None:
        with patch.object(notification_tools, "notification_service") as mock_ns:
            mock_ns.send_telegram = AsyncMock()
            result = await notification_tools.notify_order_failed(order_id="order-1")

        assert result == "NOTIFIED"

    async def test_tool_keyword_only_signature(self) -> None:
        sig = inspect.signature(notification_tools.notify_order_confirmed)
        for name, param in sig.parameters.items():
            assert param.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.VAR_KEYWORD,
            ), f"Parameter {name!r} must be keyword-only, got {param.kind}"
