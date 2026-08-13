"""tests/unit/test_zalo_client.py — mocked Zalo Open API transport client."""
from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.zalo_client import ZaloClient, ZaloProfileError


def _client(**kwargs: str) -> ZaloClient:
    defaults: dict[str, str] = {
        "app_id": "app-1",
        "secret_key": "secret-1",
    }
    defaults.update(kwargs)
    return ZaloClient(**defaults)  # type: ignore[arg-type]


class TestZaloClientConfigured:
    def test_configured_true_with_both_app_id_and_secret(self) -> None:
        assert _client().configured is True

    def test_configured_false_without_app_id(self) -> None:
        assert _client(app_id="").configured is False

    def test_configured_false_without_secret(self) -> None:
        assert _client(secret_key="").configured is False


class TestZaloClientAppsecretProof:
    def test_appsecret_proof_matches_hmac_sha256(self) -> None:
        client = _client(secret_key="my-secret")
        expected = hmac.new(
            b"my-secret", b"token-abc", hashlib.sha256
        ).hexdigest()

        assert client._appsecret_proof("token-abc") == expected


class TestZaloClientGetUserProfile:
    async def test_get_user_profile_success_returns_data(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "zalo-uid-1", "name": "Иван"}
        client = _client()

        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=mock_response)):
            profile = await client.get_user_profile("token-abc")

        assert profile == {"id": "zalo-uid-1", "name": "Иван"}

    async def test_get_user_profile_sends_appsecret_proof_header(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "zalo-uid-1"}
        client = _client(secret_key="my-secret")
        expected_proof = hmac.new(
            b"my-secret", b"token-abc", hashlib.sha256
        ).hexdigest()

        with patch.object(
            httpx.AsyncClient, "get", AsyncMock(return_value=mock_response)
        ) as mock_get:
            await client.get_user_profile("token-abc")

        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["appsecret_proof"] == expected_proof
        assert kwargs["headers"]["access_token"] == "token-abc"

    async def test_get_user_profile_not_configured_raises(self) -> None:
        client = _client(app_id="", secret_key="")

        with pytest.raises(ZaloProfileError):
            await client.get_user_profile("token-abc")

    async def test_get_user_profile_error_payload_raises(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": -216, "message": "Invalid token"}
        client = _client()

        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=mock_response)):
            with pytest.raises(ZaloProfileError):
                await client.get_user_profile("bad-token")

    async def test_get_user_profile_missing_id_raises(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"name": "no id field"}
        client = _client()

        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=mock_response)):
            with pytest.raises(ZaloProfileError):
                await client.get_user_profile("token-abc")

    async def test_get_user_profile_http_error_raises(self) -> None:
        request = httpx.Request("GET", "https://graph.zalo.me/v2.0/me")
        response = httpx.Response(401, request=request, text="unauthorized")
        client = _client()

        with patch.object(
            httpx.AsyncClient,
            "get",
            AsyncMock(side_effect=httpx.HTTPStatusError("401", request=request, response=response)),
        ):
            with pytest.raises(ZaloProfileError):
                await client.get_user_profile("token-abc")

    async def test_get_user_profile_request_error_raises(self) -> None:
        client = _client()

        with patch.object(
            httpx.AsyncClient,
            "get",
            AsyncMock(side_effect=httpx.RequestError("connection failed")),
        ):
            with pytest.raises(ZaloProfileError):
                await client.get_user_profile("token-abc")

    async def test_get_user_profile_json_parse_error_raises(self) -> None:
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("not json")
        client = _client()

        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=mock_response)):
            with pytest.raises(ZaloProfileError):
                await client.get_user_profile("token-abc")


class TestZaloClientProxyConfig:
    def test_empty_proxy_url_resolves_to_none(self) -> None:
        client = _client(proxy_url="")
        assert client._proxy is None

    def test_none_proxy_url_falls_back_to_settings_default(self) -> None:
        # settings.ZALO_API_PROXY_URL defaults to "" in a clean test env,
        # so omitting proxy_url entirely also resolves to None.
        client = _client()
        assert client._proxy is None

    def test_explicit_proxy_url_is_kept(self) -> None:
        client = _client(proxy_url="http://proxy.example.vn:8080")
        assert client._proxy == "http://proxy.example.vn:8080"
