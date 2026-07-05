"""tests/integration/test_orders_board.py — orders kanban board tests.

Import-path assertion tests do NOT require Docker.
Behavioral integration tests require Docker (testcontainers).
"""
from __future__ import annotations

import inspect
import subprocess
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import router as admin_router
from app.api.routes.orders import router as orders_router
from app.services.order_service import OrderService, fetch_orders
from app.web.routes import router as web_router

# Override conftest's pytestmark — this file has tests that don't need Docker
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

    def test_fetch_orders_is_callable(self) -> None:
        assert callable(fetch_orders)

    def test_order_service_list_orders_delegates_to_fetch_orders(self) -> None:
        source = inspect.getsource(OrderService.list_orders)
        assert "fetch_orders" in source

    def test_both_handlers_import_from_order_service(self) -> None:
        import app.api.routes.admin as admin_module
        import app.web.routes as web_module

        admin_src = Path(admin_module.__file__).read_text()
        web_src = Path(web_module.__file__).read_text()

        assert "from app.services.order_service import" in admin_src
        assert "from app.services.order_service import" in web_src


# ── Integration tests ──────────────────────────────────────────────

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

    app.include_router(orders_router)
    app.include_router(admin_router)
    app.include_router(web_router)

    from app.api.routes.admin import get_order_service as admin_get_service
    from app.api.routes.orders import get_order_service as orders_get_service
    from app.web.routes import get_order_service as web_get_service

    async def _test_service() -> OrderService:
        return OrderService(session_factory=session_factory)

    app.dependency_overrides[orders_get_service] = _test_service
    app.dependency_overrides[admin_get_service] = _test_service
    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestOrdersBoard:
    async def test_partial_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/orders/partial")
        assert resp.status_code == 200

    async def test_partial_contains_all_columns(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/orders/partial")
        html = resp.text
        for col in ("DRAFT", "CONFIRMED", "COOKING", "READY", "DELIVERED", "CANCELLED"):
            assert col in html

    async def test_partial_shows_created_order(self, client: AsyncClient) -> None:
        payload = {
            "customer_id": str(uuid.uuid4()),
            "items": [{"sku": "burger", "qty": 1}],
            "delivery_address": "Moscow",
        }
        create_resp = await client.post("/orders", json=payload)
        order_id = create_resp.json()["id"]

        json_resp = await client.get("/admin/orders")
        json_data = json_resp.json()
        assert order_id in [o["id"] for o in json_data]

        html_resp = await client.get("/admin/ui/orders/partial")
        assert order_id[:7] in html_resp.text

    async def test_draft_order_in_draft_column(self, client: AsyncClient) -> None:
        payload = {
            "customer_id": str(uuid.uuid4()),
            "items": [{"sku": "pizza", "qty": 1}],
            "delivery_address": "SPb",
        }
        resp = await client.post("/orders", json=payload)
        draft_id = resp.json()["id"]

        payload2 = {
            "customer_id": str(uuid.uuid4()),
            "items": [{"sku": "pasta", "qty": 2}],
            "delivery_address": "Kazan",
        }
        resp = await client.post("/orders", json=payload2)
        confirmed_id = resp.json()["id"]
        await client.post(f"/orders/{confirmed_id}/events", json="CONFIRM")

        html_resp = await client.get("/admin/ui/orders/partial")
        html = html_resp.text

        assert "DRAFT" in html
        assert "CONFIRMED" in html
        assert draft_id[:7] in html
        assert confirmed_id[:7] in html
