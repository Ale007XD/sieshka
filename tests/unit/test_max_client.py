"""tests/unit/test_max_client.py — mocked MAX Bot API transport client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.max_client import MaxClient


class TestMaxClientSendMessage:
    async def test_send_message_success_returns_mid(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "message": {"body": {"mid": "mid-123"}},
        }
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)):
            mid = await client.send_message(user_id=111, text="Hello")

        assert mid == "mid-123"

    async def test_send_message_success_false_but_mid_present_still_delivered(
        self,
    ) -> None:
        """Known MAX API quirk with inline_keyboard: success=false but mid present."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": False,
            "message": {"body": {"mid": "mid-456"}},
        }
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)):
            mid = await client.send_message(user_id=111, text="Hello")

        assert mid == "mid-456"

    async def test_send_message_success_false_no_mid_returns_none(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False}
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)):
            mid = await client.send_message(user_id=111, text="Hello")

        assert mid is None

    async def test_send_message_no_token_skips_call(self) -> None:
        client = MaxClient(token="")

        with patch.object(httpx.AsyncClient, "post", AsyncMock()) as mock_post:
            mid = await client.send_message(user_id=111, text="Hello")

        assert mid is None
        mock_post.assert_not_awaited()

    async def test_send_message_http_error_returns_none(self) -> None:
        client = MaxClient(token="test-token")

        with patch.object(
            httpx.AsyncClient,
            "post",
            AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "error", request=MagicMock(), response=MagicMock(status_code=401, text="")
                )
            ),
        ):
            mid = await client.send_message(user_id=111, text="Hello")

        assert mid is None

    async def test_send_message_request_error_returns_none(self) -> None:
        client = MaxClient(token="test-token")

        with patch.object(
            httpx.AsyncClient,
            "post",
            AsyncMock(side_effect=httpx.RequestError("timeout")),
        ):
            mid = await client.send_message(user_id=111, text="Hello")

        assert mid is None

    async def test_send_message_passes_attachments(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True, "message": {"body": {"mid": "m"}}}
        client = MaxClient(token="test-token")
        attachments = [{"type": "inline_keyboard", "payload": {"buttons": []}}]

        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)
        ) as mock_post:
            await client.send_message(user_id=111, text="Hi", attachments=attachments)

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["attachments"] == attachments


class TestMaxClientEditMessage:
    async def test_edit_message_success(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "put", AsyncMock(return_value=mock_response)):
            ok = await client.edit_message(message_id="mid-1", text="Updated")

        assert ok is True

    async def test_edit_message_failure(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False}
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "put", AsyncMock(return_value=mock_response)):
            ok = await client.edit_message(message_id="mid-1", text="Updated")

        assert ok is False

    async def test_edit_message_no_token_skips_call(self) -> None:
        client = MaxClient(token="")

        with patch.object(httpx.AsyncClient, "put", AsyncMock()) as mock_put:
            ok = await client.edit_message(message_id="mid-1", text="Updated")

        assert ok is False
        mock_put.assert_not_awaited()


class TestMaxClientAnswerCallback:
    async def test_answer_callback_success(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)):
            ok = await client.answer_callback(callback_id="cb-1", notification="OK")

        assert ok is True

    async def test_answer_callback_failure(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": False}
        client = MaxClient(token="test-token")

        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)):
            ok = await client.answer_callback(callback_id="cb-1")

        assert ok is False

    async def test_answer_callback_no_token_skips_call(self) -> None:
        client = MaxClient(token="")

        with patch.object(httpx.AsyncClient, "post", AsyncMock()) as mock_post:
            ok = await client.answer_callback(callback_id="cb-1")

        assert ok is False
        mock_post.assert_not_awaited()

    async def test_answer_callback_with_message_update_builds_body(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"success": True}
        client = MaxClient(token="test-token")

        with patch.object(
            httpx.AsyncClient, "post", AsyncMock(return_value=mock_response)
        ) as mock_post:
            await client.answer_callback(
                callback_id="cb-1", message_text="Updated text", attachments=[]
            )

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["message"]["text"] == "Updated text"


class TestMaxClientConfig:
    def test_configured_true_with_token(self) -> None:
        assert MaxClient(token="x").configured is True

    def test_configured_false_without_token(self) -> None:
        assert MaxClient(token="").configured is False

    def test_default_base_url_is_platform_api2(self) -> None:
        client = MaxClient(token="x")
        assert client._base_url == "https://platform-api2.max.ru"

    def test_explicit_base_url_overrides_default(self) -> None:
        client = MaxClient(token="x", base_url="https://custom.example.com")
        assert client._base_url == "https://custom.example.com"