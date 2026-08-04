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

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

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
        await conn.execute("TRUNCATE TABLE inventory")
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def session_factory_with_menu(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """001+004+015 — products.sku present, used by TestInventorySync.
    Separate from session_factory (001-only) so existing tests above stay
    untouched — they don't need products at all."""
    engine = create_async_engine(postgres_dsn)
    schema_paths = [
        _MIGRATIONS_DIR / "001_initial_schema.sql",
        _MIGRATIONS_DIR / "004_menu.sql",
        _MIGRATIONS_DIR / "015_products_sku.sql",
    ]
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        for sp in schema_paths:
            await conn.execute(sp.read_text())
        # products.category_id → categories(id) FK — CASCADE, same reason as
        # test_menu_repo.py (TRUNCATE categories alone fails on FK reference)
        await conn.execute("TRUNCATE TABLE inventory, products, categories CASCADE")
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def client_with_menu(
    session_factory_with_menu: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    app.include_router(web_router)

    from app.web.routes import get_inventory_service as web_get_service

    async def _test_service() -> InventoryService:
        return InventoryService(session_factory=session_factory_with_menu)

    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db(postgres_dsn: str) -> AsyncGenerator[asyncpg.Connection, None]:
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        yield conn
    finally:
        await conn.close()


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

    async def test_partial_shows_seeded_item(
        self, client: AsyncClient, db: asyncpg.Connection,
    ) -> None:
        await db.execute(
            "INSERT INTO inventory (sku, name, quantity, state) "
            "VALUES ($1, $2, $3, $4)",
            "BURGER-001", "Classic Burger", 50, InventoryState.AVAILABLE.value,
        )
        resp = await client.get("/admin/ui/inventory/partial")
        assert "BURGER-001" in resp.text
        assert "Classic Burger" in resp.text
        assert "50" in resp.text
        assert InventoryState.AVAILABLE.value in resp.text

    async def test_low_stock_row_has_amber_class(
        self, client: AsyncClient, db: asyncpg.Connection,
    ) -> None:
        await db.execute(
            "INSERT INTO inventory (sku, name, quantity, state) "
            "VALUES ($1, $2, $3, $4)",
            "FRIES-001", "French Fries", 10, InventoryState.LOW_STOCK.value,
        )
        resp = await client.get("/admin/ui/inventory/partial")
        assert "bg-amber-50" in resp.text


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestInventorySync:
    """sprint_inventory_menu_sync — products.sku → inventory get_or_create.
    See migrations/015_products_sku.sql, DECISIONS.md."""

    async def _insert_product(
        self, db: asyncpg.Connection, name: str, sku: str | None,
    ) -> None:
        await db.execute(
            "INSERT INTO products (name, sku, is_active) VALUES ($1, $2, TRUE)",
            name, sku,
        )

    async def test_sync_creates_inventory_row_for_product_with_sku(
        self, client_with_menu: AsyncClient, db: asyncpg.Connection,
    ) -> None:
        await self._insert_product(db, "Classic Burger", "BURGER-001")
        resp = await client_with_menu.post("/admin/ui/inventory/sync")
        assert resp.status_code == 200
        assert "BURGER-001" in resp.text
        assert "1 new item(s) created" in resp.text
        row = await db.fetchrow(
            "SELECT quantity, state FROM inventory WHERE sku = $1", "BURGER-001",
        )
        assert row is not None
        assert row["quantity"] == 0
        assert row["state"] == "OUT_OF_STOCK"

    async def test_sync_skips_product_without_sku(
        self, client_with_menu: AsyncClient, db: asyncpg.Connection,
    ) -> None:
        await self._insert_product(db, "Unassigned Item", None)
        resp = await client_with_menu.post("/admin/ui/inventory/sync")
        assert resp.status_code == 200
        assert "0 new item(s) created" in resp.text
        assert "1 active product(s) skipped" in resp.text

    async def test_sync_is_idempotent(
        self, client_with_menu: AsyncClient, db: asyncpg.Connection,
    ) -> None:
        await self._insert_product(db, "Classic Burger", "BURGER-001")
        await client_with_menu.post("/admin/ui/inventory/sync")
        resp = await client_with_menu.post("/admin/ui/inventory/sync")
        assert "0 new item(s) created" in resp.text
        count = await db.fetchval("SELECT COUNT(*) FROM inventory WHERE sku = $1", "BURGER-001")
        assert count == 1

    async def test_sync_does_not_overwrite_existing_stock(
        self, client_with_menu: AsyncClient, db: asyncpg.Connection,
    ) -> None:
        await self._insert_product(db, "Classic Burger", "BURGER-001")
        await db.execute(
            "INSERT INTO inventory (sku, name, quantity, state) "
            "VALUES ($1, $2, $3, $4)",
            "BURGER-001", "Classic Burger", 42, InventoryState.AVAILABLE.value,
        )
        resp = await client_with_menu.post("/admin/ui/inventory/sync")
        assert "0 new item(s) created" in resp.text
        row = await db.fetchrow(
            "SELECT quantity, state FROM inventory WHERE sku = $1", "BURGER-001",
        )
        assert row is not None
        assert row["quantity"] == 42
        assert row["state"] == InventoryState.AVAILABLE.value
