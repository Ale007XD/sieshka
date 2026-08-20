"""tests/integration/test_messengers_admin.py — Messengers admin panel HTTP wiring.

Requires Docker (real Postgres). Mirrors test_menu_import_endpoint.py's app
assembly exactly: admin_router mounted with the same auth dependency as
app/main.py, StaffService overridden to use the test session factory.

sprint_max_admin_panel: closes the UI gap sprint_staff_table's own docstring
flagged as deferred ("staff rows are seeded directly (psql / a future admin
screen)"). staff CRUD is deliberately NOT routed through a nano-vm Agent
(unlike menu/zones/promotions elsewhere in admin.py) — see
app/services/staff_service.py's module docstring for why.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from fastapi import Depends, FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import get_staff_service
from app.api.routes.admin import router as admin_router
from app.domains.staff.models import StaffRole
from app.services.staff_service import StaffService
from app.web.auth import get_current_username

pytestmark = [pytest.mark.integration]

DASHBOARD_PASSWORD = "test-password"


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_paths = [
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql",
        Path(__file__).resolve().parents[2] / "migrations" / "017_staff.sql",
        Path(__file__).resolve().parents[2] / "migrations" / "020_staff_role_staff.sql",
        Path(__file__).resolve().parents[2] / "migrations" / "021_staff_zalo_user_id.sql",
    ]
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        for sp in schema_paths:
            await conn.execute(sp.read_text())
        await conn.execute("TRUNCATE TABLE staff")
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(admin_router, dependencies=[Depends(get_current_username)])

    def _test_service() -> StaffService:
        return StaffService(session_factory=session_factory)

    app.dependency_overrides[get_staff_service] = _test_service

    templates_dir = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_staff(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str = "Повар Иван",
    role: str = "kitchen",
    max_user_id: int | None = 111,
    telegram_user_id: int | None = None,
    zalo_user_id: str | None = None,
) -> str:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "INSERT INTO staff (name, role, max_user_id, telegram_user_id, "
                "zalo_user_id, active) "
                "VALUES (:name, :role, :max_user_id, :telegram_user_id, "
                ":zalo_user_id, true) "
                "RETURNING id"
            ),
            {
                "name": name,
                "role": role,
                "max_user_id": max_user_id,
                "telegram_user_id": telegram_user_id,
                "zalo_user_id": zalo_user_id,
            },
        )
        row = result.fetchone()
        await session.commit()
        assert row is not None
        return str(row._mapping["id"])


class TestMessengersAdminAuth:
    async def test_ui_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/messengers")
        assert resp.status_code == 401

    async def test_staff_apply_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "X", "role": "kitchen", "max_user_id": 1},
        )
        assert resp.status_code == 401


class TestMessengersAdminUi:
    async def test_ui_renders_with_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/messengers", auth=("admin", DASHBOARD_PASSWORD))
        assert resp.status_code == 200
        assert b"Messengers" in resp.content
        assert b"MAX" in resp.content
        assert b"Telegram" in resp.content
        assert b"Zalo" in resp.content

    async def test_ui_shows_seeded_staff(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_staff(session_factory, name="Повар Иван", max_user_id=111)
        resp = await client.get("/admin/ui/messengers", auth=("admin", DASHBOARD_PASSWORD))
        assert resp.status_code == 200
        # Cyrillic is JSON/unicode-escaped in the tojson blob, not literal —
        # assert on the escaped max_user_id int instead, which round-trips as-is.
        assert b'"max_user_id": 111' in resp.content or b'"max_user_id":111' in resp.content


class TestStaffApply:
    async def test_create_max_account(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "Повар Иван", "role": "kitchen", "max_user_id": 111},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert any(s["max_user_id"] == 111 for s in data["staff"])

        async with session_factory() as s:
            res = await s.execute(text("SELECT name, role FROM staff WHERE max_user_id=111"))
            row = res.fetchone()
            assert row is not None
            assert row._mapping["name"] == "Повар Иван"
            assert row._mapping["role"] == "kitchen"

    async def test_create_two_telegram_accounts_same_role(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Two staff rows, same role, same channel (Telegram), different
        telegram_user_id — must both succeed. role has no UNIQUE constraint
        (migrations/017_staff.sql); only max_user_id/telegram_user_id/
        zalo_user_id are independently unique per-column. Regression guard:
        the only existing dup-rejection tests (test_create_duplicate_
        max_user_id_rejected, ..._zalo_user_id_rejected) exercise the SAME-id
        conflict path — none previously asserted the SAME-role, DIFFERENT-id
        path actually succeeds instead of silently being blocked by some
        other constraint (e.g. a stray composite unique index)."""
        resp1 = await client.post(
            "/admin/staff/apply",
            json={"name": "Повар Иван", "role": "kitchen", "telegram_user_id": 501},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp1.status_code == 200, resp1.text
        data1 = resp1.json()
        assert data1["ok"] is True, data1

        resp2 = await client.post(
            "/admin/staff/apply",
            json={"name": "Повар Пётр", "role": "kitchen", "telegram_user_id": 502},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp2.status_code == 200, resp2.text
        data2 = resp2.json()
        assert data2["ok"] is True, data2

        # Both rows present in the same list_all() snapshot the admin UI
        # renders from — not just two independent 200 OKs.
        kitchen_telegram_ids = {
            s["telegram_user_id"]
            for s in data2["staff"]
            if s["role"] == "kitchen" and s["telegram_user_id"] is not None
        }
        assert {501, 502} <= kitchen_telegram_ids

        # The actual consumer of this data (StaffService.list_active_by_role,
        # called from app.services.max_staff_notify's broadcast loop) sees
        # both — this is the assertion that matters for "notification goes
        # to N accounts on one role", not just "the row exists".
        service = StaffService(session_factory=session_factory)
        recipients = await service.list_active_by_role(StaffRole.kitchen)
        assert {r.telegram_user_id for r in recipients} == {501, 502}

    async def test_create_missing_name_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "", "role": "kitchen", "max_user_id": 111},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    async def test_create_invalid_role_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "X", "role": "manager", "max_user_id": 111},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "role" in data["error"]

    async def test_create_duplicate_max_user_id_rejected(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_staff(session_factory, max_user_id=111)
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "Second", "role": "courier", "max_user_id": 111},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "already assigned" in data["error"]

    async def test_create_non_integer_max_user_id_rejected(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "X", "role": "kitchen", "max_user_id": "not-a-number"},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    async def test_create_zalo_account(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "Курьер Зало", "role": "courier", "zalo_user_id": "zalo-uid-1"},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert any(s["zalo_user_id"] == "zalo-uid-1" for s in data["staff"])

        async with session_factory() as s:
            res = await s.execute(
                text("SELECT name, role FROM staff WHERE zalo_user_id='zalo-uid-1'")
            )
            row = res.fetchone()
            assert row is not None
            assert row._mapping["name"] == "Курьер Зало"

    async def test_create_duplicate_zalo_user_id_rejected(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_staff(session_factory, max_user_id=None, zalo_user_id="zalo-uid-1")
        resp = await client.post(
            "/admin/staff/apply",
            json={"name": "Second", "role": "courier", "zalo_user_id": "zalo-uid-1"},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "already assigned" in data["error"]


class TestStaffUpdate:
    async def test_update_name_and_role(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        staff_id = await _seed_staff(session_factory, name="Old Name", role="kitchen")
        resp = await client.patch(
            f"/admin/staff/{staff_id}/apply",
            json={"name": "New Name", "role": "admin"},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        updated = next(s for s in data["staff"] if s["id"] == staff_id)
        assert updated["name"] == "New Name"
        assert updated["role"] == "admin"

    async def test_deactivate(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        staff_id = await _seed_staff(session_factory)
        resp = await client.patch(
            f"/admin/staff/{staff_id}/apply",
            json={"active": False},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        updated = next(s for s in data["staff"] if s["id"] == staff_id)
        assert updated["active"] is False

    async def test_unlink_max_user_id_via_explicit_null(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        staff_id = await _seed_staff(session_factory, max_user_id=111)
        resp = await client.patch(
            f"/admin/staff/{staff_id}/apply",
            json={"max_user_id": None},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        updated = next(s for s in data["staff"] if s["id"] == staff_id)
        assert updated["max_user_id"] is None

    async def test_unlink_zalo_user_id_via_explicit_null(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        staff_id = await _seed_staff(
            session_factory, max_user_id=None, zalo_user_id="zalo-uid-1"
        )
        resp = await client.patch(
            f"/admin/staff/{staff_id}/apply",
            json={"zalo_user_id": None},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        updated = next(s for s in data["staff"] if s["id"] == staff_id)
        assert updated["zalo_user_id"] is None

    async def test_absent_field_leaves_value_unchanged(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Key-presence PATCH semantics: max_user_id absent from the payload
        must NOT be touched, unlike an explicit null (previous test)."""
        staff_id = await _seed_staff(session_factory, max_user_id=111, name="Original")
        resp = await client.patch(
            f"/admin/staff/{staff_id}/apply",
            json={"name": "Renamed"},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        updated = next(s for s in data["staff"] if s["id"] == staff_id)
        assert updated["name"] == "Renamed"
        assert updated["max_user_id"] == 111  # untouched

    async def test_update_unknown_id_returns_404(self, client: AsyncClient) -> None:
        resp = await client.patch(
            "/admin/staff/00000000-0000-0000-0000-000000000000/apply",
            json={"name": "X"},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 404

    async def test_update_to_conflicting_max_user_id_rejected(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_staff(session_factory, max_user_id=111, name="A")
        staff_b = await _seed_staff(session_factory, max_user_id=222, name="B")
        resp = await client.patch(
            f"/admin/staff/{staff_b}/apply",
            json={"max_user_id": 111},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "already assigned" in data["error"]
