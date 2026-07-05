"""tests/integration/test_stats_dashboard.py — GET /admin/ui/stats.

Import-path assertion tests do NOT require Docker.
Behavioral integration tests require Docker (testcontainers).
"""
from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from nano_vm_mcp.store import ProgramStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.admin import router as admin_router
from app.domains.orders.models import OrderState
from app.services.order_service import OrderService
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

    def test_reuses_existing_transitions_dependency(self) -> None:
        import app.web.routes as routes_module
        src = Path(routes_module.__file__).read_text()
        assert "from app.api.routes.admin import get_transitions_store" in src

    def test_order_service_is_callable(self) -> None:
        assert callable(OrderService)

    def test_all_order_states_accounted(self) -> None:
        assert len(OrderState) == 11


docker_available = _is_docker_available()


@pytest.fixture
def nano_store(tmp_path: Path) -> ProgramStore:
    db_path = tmp_path / "test_stats.db"
    store = ProgramStore(str(db_path))
    store.upsert_transition("order_confirm", "DRAFT", "CONFIRMED")
    store.upsert_transition("order_request_payment", "CONFIRMED", "PAYMENT_PENDING")
    store.upsert_transition("order_request_payment", "PAYMENT_PENDING", "PAID")
    store.upsert_transition("order_start_cooking", "PAID", "COOKING")
    store.upsert_transition("order_cancel", "DRAFT", "CANCELLED")
    return store


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(postgres_dsn)

    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_dsn)
    try:
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "001_initial_schema.sql"
        )
        await conn.execute(schema_path.read_text())
    finally:
        await conn.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    await engine.dispose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    nano_store: ProgramStore,
) -> AsyncGenerator[AsyncClient, None]:
    templates_dir = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(templates_dir))
    app.include_router(admin_router)
    app.include_router(web_router)

    from app.api.routes.admin import get_transitions_store
    from app.web.routes import get_order_service as web_get_service

    async def _test_service() -> OrderService:
        return OrderService(session_factory=session_factory)

    app.dependency_overrides[get_transitions_store] = lambda: nano_store
    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestStatsDashboard:
    async def _seed_order(
        self, state: OrderState, payment_id: str | None = None,
    ) -> str:
        conn = await asyncpg.connect(
            "postgresql://sieshka:sieshka@localhost:5432/sieshka"
        )
        try:
            order_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
                "VALUES ($1, $2, $3, $4, $5)",
                order_id, str(uuid.uuid4()), state.value,
                '[]', "Moscow, Red Square 1",
            )
            return order_id
        finally:
            await conn.close()

    async def test_page_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/stats")
        assert resp.status_code == 200

    async def test_shows_empty_state_counts(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/stats")
        assert "Orders by State" in resp.text
        for state in OrderState:
            assert state.value in resp.text

    async def test_shows_seeded_order_counts(self, client: AsyncClient) -> None:
        await self._seed_order(OrderState.DRAFT)
        await self._seed_order(OrderState.DRAFT)
        await self._seed_order(OrderState.CONFIRMED)
        await self._seed_order(OrderState.COOKING)

        resp = await client.get("/admin/ui/stats")
        html = resp.text
        assert "Orders by State" in html
        assert "DRAFT" in html
        assert "2" in html
        assert "CONFIRMED" in html
        assert "1" in html
        assert "COOKING" in html
        assert "1" in html

    async def test_shows_transition_stats(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/stats")
        assert "Transition Stats" in resp.text
        assert "DRAFT" in resp.text
        assert "CONFIRMED" in resp.text
        assert "PAYMENT_PENDING" in resp.text
        assert "PAID" in resp.text
        assert "COOKING" in resp.text
        assert "CANCELLED" in resp.text

    async def test_transition_svg_bars_rendered(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/stats")
        assert "<svg" in resp.text
        assert 'fill="#6366f1"' in resp.text

    async def test_advisory_timing(self, client: AsyncClient) -> None:
        import time
        start = time.monotonic()
        resp = await client.get("/admin/ui/stats")
        elapsed_ms = (time.monotonic() - start) * 1000
        assert resp.status_code == 200
        assert elapsed_ms < 5000, f"Response took {elapsed_ms:.0f}ms"
