"""tests/integration/test_menu_import_endpoint.py — HTTP wiring for menu import.

Requires Docker (real Postgres). Skipped if unavailable. Uses httpx AsyncClient
against a fresh FastAPI app that mounts admin_router exactly like app/main.py
(auth applied at include time), with the MenuImportService dependency overridden
to use the test session factory.

Verifies the concrete "before → after" of this sprint: a real multipart POST to
/admin/menu/import-csv (with admin Basic auth) actually writes a product row to
Postgres, and GET /admin/ui/menu renders; both are 401 without auth.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import (
    get_menu_import_service,
)
from app.api.routes.admin import (
    router as admin_router,
)
from app.services.menu_import_service import MenuImportService
from app.web.auth import get_current_username

pytestmark = [pytest.mark.integration]

DASHBOARD_PASSWORD = "test-password"


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = (
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    )
    schema = schema_path.read_text()

    import asyncpg

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute(schema)
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
    app.include_router(
        admin_router, dependencies=[Depends(get_current_username)]
    )

    async def _test_service() -> MenuImportService:
        return MenuImportService(session_factory=session_factory)

    app.dependency_overrides[get_menu_import_service] = _test_service

    # Replicate app/main.py's template wiring so GET /admin/ui/menu renders.
    templates_dir = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac


async def _seed_category(session: AsyncSession) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO categories (external_id, name, menu_period, sort, is_active) "
            "VALUES ('1', 'Бургеры', 'both', 10, TRUE)"
        )
    )
    await session.commit()


class TestMenuImportEndpoint:
    async def test_import_requires_auth(self, client: AsyncClient) -> None:
        csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,1,,350,\n"
        resp = await client.post(
            "/admin/menu/import-csv",
            files={"file": ("menu.csv", csv.encode(), "text/csv")},
        )
        assert resp.status_code == 401

    async def test_ui_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/menu")
        assert resp.status_code == 401

    async def test_import_writes_product_with_auth(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with session_factory() as s:
            await s.execute(text("DELETE FROM products"))
            await s.execute(text("DELETE FROM categories"))
            await s.commit()
            await _seed_category(s)

        csv = "Name,Category,Description,Price Rub,Photo Url\nBurger,1,tasty,350,http://x/y.png\n"
        resp = await client.post(
            "/admin/menu/import-csv",
            files={"file": ("menu.csv", csv.encode(), "text/csv")},
            auth=("admin", DASHBOARD_PASSWORD),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["imported"] == 1
        assert data["final_status"] == "success"
        assert data["skipped"] == []

        # The concrete proof: a real product row landed in Postgres.
        async with session_factory() as s:
            res = await s.execute(
                text("SELECT name, price_rub, is_active FROM products")
            )
            rows = res.fetchall()
            assert len(rows) == 1
            assert rows[0]._mapping["name"] == "Burger"
            assert rows[0]._mapping["price_rub"] == 350

    async def test_ui_renders_with_auth(
        self,
        client: AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        resp = await client.get(
            "/admin/ui/menu", auth=("admin", DASHBOARD_PASSWORD)
        )
        assert resp.status_code == 200
        assert b"Menu" in resp.content
