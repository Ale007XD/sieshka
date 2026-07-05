"""tests/integration/test_inventory_panel.py — stock levels panel tests.

Import-path assertion tests do NOT require Docker.
Behavioral integration tests require Docker (testcontainers).
"""
from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.inventory.models import InventoryState
from app.services.inventory_service import InventoryService
from app.web.helpers import INVENTORY_STATE_COLOR
from app.web.routes import router as web_router

pytestmark: list[object] = []


def _is_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


class TestImportPath:
    """Import-path assertions — no Docker required."""

    def test_inventory_state_color_has_all_states(self) -> None:
        for state in InventoryState:
            assert state in INVENTORY_STATE_COLOR

    def test_inventory_service_is_callable(self) -> None:
        assert callable(InventoryService)

    def test_helpers_import_from_inventory_models(self) -> None:
        import app.web.helpers as helpers_module

        src = Path(helpers_module.__file__).read_text()
        assert "from app.domains.inventory.models import InventoryState" in src


docker_available = _is_docker_available()


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)
    schema_path = (
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
    )
    schema = schema_path.read_text()

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
    templates_dir = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    app.include_router(web_router)

    from app.web.routes import get_inventory_service as web_get_service

    async def _test_service() -> InventoryService:
        return InventoryService(session_factory=session_factory)

    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestInventoryPanel:
    async def test_partial_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/inventory/partial")
        assert resp.status_code == 200

    async def test_partial_shows_empty_message(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/inventory/partial")
        assert "No inventory items found" in resp.text

    async def test_partial_shows_seeded_item(self, client: AsyncClient) -> None:
        conn = await asyncpg.connect(
            "postgresql://sieshka:sieshka@localhost:5432/sieshka"
        )
        try:
            await conn.execute(
                "INSERT INTO inventory (sku, name, quantity, state) "
                "VALUES ($1, $2, $3, $4)",
                "BURGER-001", "Classic Burger", 50, InventoryState.AVAILABLE.value,
            )
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/inventory/partial")
        assert "BURGER-001" in resp.text
        assert "Classic Burger" in resp.text
        assert "50" in resp.text
        assert InventoryState.AVAILABLE.value in resp.text

    async def test_low_stock_row_has_amber_class(self, client: AsyncClient) -> None:
        conn = await asyncpg.connect(
            "postgresql://sieshka:sieshka@localhost:5432/sieshka"
        )
        try:
            await conn.execute(
                "INSERT INTO inventory (sku, name, quantity, state) "
                "VALUES ($1, $2, $3, $4)",
                "FRIES-001", "French Fries", 10, InventoryState.LOW_STOCK.value,
            )
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/inventory/partial")
        assert "bg-amber-50" in resp.text
