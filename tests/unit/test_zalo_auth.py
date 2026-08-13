"""tests/unit/test_zalo_auth.py — Zalo Mini App per-request auth dependency.

Mocked ZaloClient/StaffService, mirrors test_max_client.py/test_max_webhook.py's
no-DB-no-network split.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.domains.staff.models import Staff, StaffRole
from app.services.staff_service import StaffService
from app.services.zalo_client import ZaloClient, ZaloProfileError
from app.web.zalo_auth import get_current_zalo_staff, get_staff_service, get_zalo_client


def _staff(role: StaffRole = StaffRole.kitchen) -> Staff:
    return Staff(id=uuid.uuid4(), name="Test", role=role, zalo_user_id="zalo-uid-1")


class _Mocks:
    def __init__(self) -> None:
        self.staff = AsyncMock(spec=StaffService)
        self.zalo = AsyncMock(spec=ZaloClient)


@pytest.fixture
def mocks() -> _Mocks:
    return _Mocks()


@pytest.fixture
async def client(mocks: _Mocks):
    app = FastAPI()

    @app.get("/probe")
    async def probe(staff: Staff = Depends(get_current_zalo_staff)) -> dict[str, str]:
        return {"role": staff.role.value}

    app.dependency_overrides[get_staff_service] = lambda: mocks.staff
    app.dependency_overrides[get_zalo_client] = lambda: mocks.zalo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestGetCurrentZaloStaff:
    async def test_missing_header_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/probe")
        assert resp.status_code == 401

    async def test_invalid_token_returns_401(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.zalo.get_user_profile.side_effect = ZaloProfileError("bad token")

        resp = await client.get("/probe", headers={"X-Zalo-Access-Token": "bad"})

        assert resp.status_code == 401

    async def test_profile_missing_id_returns_401(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.zalo.get_user_profile.return_value = {"name": "no id"}

        resp = await client.get("/probe", headers={"X-Zalo-Access-Token": "tok"})

        assert resp.status_code == 401

    async def test_unknown_staff_returns_403(
        self, client: AsyncClient, mocks: _Mocks
    ) -> None:
        mocks.zalo.get_user_profile.return_value = {"id": "zalo-uid-1"}
        mocks.staff.find_by_zalo_user_id.return_value = None

        resp = await client.get("/probe", headers={"X-Zalo-Access-Token": "tok"})

        assert resp.status_code == 403

    async def test_valid_staff_resolves(self, client: AsyncClient, mocks: _Mocks) -> None:
        mocks.zalo.get_user_profile.return_value = {"id": "zalo-uid-1"}
        mocks.staff.find_by_zalo_user_id.return_value = _staff(StaffRole.courier)

        resp = await client.get("/probe", headers={"X-Zalo-Access-Token": "tok"})

        assert resp.status_code == 200
        assert resp.json() == {"role": "courier"}
        mocks.staff.find_by_zalo_user_id.assert_awaited_once_with("zalo-uid-1")
