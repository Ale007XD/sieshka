"""tests/unit/test_admin_staff_zalo.py — staff_apply/staff_update's
zalo_user_id handling (sprint_zalo_admin_panel), fully mocked StaffService.

Supplements tests/integration/test_messengers_admin.py (Docker/Postgres-
required, not runnable in this sandbox) with a live-run, no-DB smoke check
of the branches this sprint actually touched: zalo_user_id is a string, not
an int like max_user_id/telegram_user_id — that's the one place this
sprint's logic diverges from the existing pattern it otherwise mirrors
exactly, so it's the one place worth an independent check beyond ruff/mypy.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.admin import get_staff_service
from app.api.routes.admin import router as admin_router
from app.domains.staff.models import Staff, StaffRole
from app.services.staff_service import StaffConflictError, StaffService
from app.web.auth import get_current_username

_ClientAndMock = tuple[AsyncClient, AsyncMock]


def _staff_row(**over: object) -> Staff:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "Курьер Зало",
        "role": StaffRole.courier,
        "max_user_id": None,
        "telegram_user_id": None,
        "zalo_user_id": "zalo-uid-1",
        "active": True,
    }
    base.update(over)
    return Staff(**base)  # type: ignore[arg-type]


@pytest.fixture
async def client() -> AsyncGenerator[_ClientAndMock, None]:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_current_username] = lambda: "admin"
    svc = AsyncMock(spec=StaffService)
    app.dependency_overrides[get_staff_service] = lambda: svc
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, svc


class TestStaffApplyZaloBranch:
    async def test_zalo_user_id_passed_as_string_not_int(self, client: _ClientAndMock) -> None:
        ac, svc = client
        svc.create = AsyncMock(return_value=_staff_row())
        svc.list_all = AsyncMock(return_value=[_staff_row()])

        resp = await ac.post(
            "/admin/staff/apply",
            json={"name": "Курьер Зало", "role": "courier", "zalo_user_id": "zalo-uid-1"},
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        svc.create.assert_awaited_once()
        _, kwargs = svc.create.call_args
        assert kwargs["zalo_user_id"] == "zalo-uid-1"
        assert isinstance(kwargs["zalo_user_id"], str)

    async def test_empty_zalo_user_id_passed_as_none(self, client: _ClientAndMock) -> None:
        ac, svc = client
        svc.create = AsyncMock(return_value=_staff_row(zalo_user_id=None))
        svc.list_all = AsyncMock(return_value=[])

        resp = await ac.post(
            "/admin/staff/apply",
            json={"name": "X", "role": "kitchen", "zalo_user_id": ""},
        )

        assert resp.status_code == 200
        _, kwargs = svc.create.call_args
        assert kwargs["zalo_user_id"] is None

    async def test_zalo_conflict_surfaces_as_ok_false(self, client: _ClientAndMock) -> None:
        ac, svc = client
        svc.create = AsyncMock(side_effect=StaffConflictError("already assigned"))
        svc.list_all = AsyncMock(return_value=[])

        resp = await ac.post(
            "/admin/staff/apply",
            json={"name": "X", "role": "kitchen", "zalo_user_id": "dup"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "already assigned" in data["error"]


class TestStaffUpdateZaloBranch:
    async def test_zalo_user_id_update_sent_as_string(self, client: _ClientAndMock) -> None:
        ac, svc = client
        staff_id = uuid.uuid4()
        svc.update = AsyncMock(return_value=_staff_row(id=staff_id))
        svc.list_all = AsyncMock(return_value=[_staff_row(id=staff_id)])

        resp = await ac.patch(
            f"/admin/staff/{staff_id}/apply", json={"zalo_user_id": "zalo-uid-2"}
        )

        assert resp.status_code == 200
        svc.update.assert_awaited_once_with(staff_id, {"zalo_user_id": "zalo-uid-2"})

    async def test_explicit_null_unlinks_zalo_user_id(self, client: _ClientAndMock) -> None:
        ac, svc = client
        staff_id = uuid.uuid4()
        svc.update = AsyncMock(return_value=_staff_row(id=staff_id, zalo_user_id=None))
        svc.list_all = AsyncMock(return_value=[])

        resp = await ac.patch(
            f"/admin/staff/{staff_id}/apply", json={"zalo_user_id": None}
        )

        assert resp.status_code == 200
        svc.update.assert_awaited_once_with(staff_id, {"zalo_user_id": None})

    async def test_absent_zalo_user_id_key_not_sent_to_service(
        self, client: _ClientAndMock
    ) -> None:
        """Key-presence PATCH: zalo_user_id absent from payload must not
        appear in the fields dict passed to StaffService.update at all —
        same guard as the existing max_user_id/telegram_user_id contract."""
        ac, svc = client
        staff_id = uuid.uuid4()
        svc.update = AsyncMock(return_value=_staff_row(id=staff_id))
        svc.list_all = AsyncMock(return_value=[])

        resp = await ac.patch(f"/admin/staff/{staff_id}/apply", json={"name": "Renamed"})

        assert resp.status_code == 200
        svc.update.assert_awaited_once_with(staff_id, {"name": "Renamed"})
