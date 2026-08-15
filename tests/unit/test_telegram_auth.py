"""tests/unit/test_telegram_auth.py — Telegram Mini App per-request staff
auth dependency (sprint_telegram_miniapp_auth).

Mocked StaffService only (no live call needed — unlike test_zalo_auth.py,
Telegram initData is offline-verifiable, same HMAC chain as
test_max_webapp_auth.py). Builds a real signed initData string independently
of app.services.max_webapp_auth, same cross-check rationale as that file's
own docstring.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.domains.staff.models import Staff, StaffRole
from app.services.staff_service import StaffService
from app.web.telegram_auth import get_current_telegram_staff, get_staff_service

_BOT_TOKEN = "test-telegram-bot-token"


def _build_init_data(*, bot_token: str = _BOT_TOKEN, user_id: int = 42) -> str:
    user_json = json.dumps({"id": user_id, "first_name": "Test"})
    params = {"user": user_json, "auth_date": str(int(time.time()))}
    launch_params = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()
    encoded = [f"{k}={quote(v, safe='')}" for k, v in params.items()]
    encoded.append(f"hash={signature}")
    return "&".join(encoded)


def _staff(role: StaffRole = StaffRole.kitchen) -> Staff:
    return Staff(id=uuid.uuid4(), name="Test", role=role, telegram_user_id=42)


class _Mocks:
    def __init__(self) -> None:
        self.staff = AsyncMock(spec=StaffService)


@pytest.fixture
def mocks() -> _Mocks:
    return _Mocks()


@pytest.fixture
async def client(mocks: _Mocks):
    app = FastAPI()

    @app.get("/probe")
    async def probe(staff: Staff = Depends(get_current_telegram_staff)) -> dict[str, str]:
        return {"role": staff.role.value}

    app.dependency_overrides[get_staff_service] = lambda: mocks.staff

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestGetCurrentTelegramStaff:
    async def test_missing_header_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/probe")
        assert resp.status_code == 401

    async def test_invalid_signature_returns_401(self, client: AsyncClient) -> None:
        with patch.object(settings, "TELEGRAM_BOT_TOKEN", _BOT_TOKEN):
            resp = await client.get(
                "/probe", headers={"X-Telegram-Init-Data": "user=%7B%7D&hash=deadbeef"}
            )
        assert resp.status_code == 401

    async def test_unknown_staff_returns_403(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.staff.find_by_telegram_user_id.return_value = None
        data = _build_init_data(user_id=999)

        with patch.object(settings, "TELEGRAM_BOT_TOKEN", _BOT_TOKEN):
            resp = await client.get("/probe", headers={"X-Telegram-Init-Data": data})

        assert resp.status_code == 403

    async def test_valid_staff_resolves(self, client: AsyncClient, mocks: _Mocks) -> None:
        mocks.staff.find_by_telegram_user_id.return_value = _staff(StaffRole.courier)
        data = _build_init_data(user_id=42)

        with patch.object(settings, "TELEGRAM_BOT_TOKEN", _BOT_TOKEN):
            resp = await client.get("/probe", headers={"X-Telegram-Init-Data": data})

        assert resp.status_code == 200
        assert resp.json() == {"role": "courier"}
        mocks.staff.find_by_telegram_user_id.assert_awaited_once_with(42)
