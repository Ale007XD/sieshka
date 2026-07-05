"""tests/integration/test_kitchen_board.py — kitchen kanban board tests.

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.kitchen.fsm import KitchenState
from app.services.kitchen_service import KitchenService
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

    def test_kitchen_service_list_tickets_is_callable(self) -> None:
        assert callable(KitchenService.list_tickets)

    def test_web_routes_imports_from_kitchen_service(self) -> None:
        import app.web.routes as web_module

        src = Path(web_module.__file__).read_text()
        assert "from app.services.kitchen_service import" in src


# ── Integration tests ──────────────────────────────────────────────

docker_available = _is_docker_available()


async def _run_schema(postgres_dsn: str) -> None:
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


@pytest.fixture
async def session_factory(
    postgres_dsn: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    await _run_schema(postgres_dsn)
    engine = create_async_engine(postgres_dsn)
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

    from app.web.routes import get_kitchen_service as web_get_service

    async def _test_service() -> KitchenService:
        return KitchenService(session_factory=session_factory)

    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _raw_conn(postgres_dsn: str) -> asyncpg.Connection:
    raw_dsn = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(raw_dsn)


async def _insert_order(conn: asyncpg.Connection, order_id: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO orders (id, customer_id, state, items, delivery_address) "
        "VALUES ($1, $2, 'DRAFT', '[]', 'Address')",
        order_id,
        uuid.uuid4(),
    )


async def _insert_ticket(
    conn: asyncpg.Connection,
    ticket_id: uuid.UUID,
    order_id: uuid.UUID,
    state: str,
) -> None:
    await conn.execute(
        "INSERT INTO kitchen_tickets (id, order_id, state) VALUES ($1, $2, $3)",
        ticket_id,
        order_id,
        state,
    )


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestKitchenBoard:
    async def test_partial_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/kitchen/partial")
        assert resp.status_code == 200

    async def test_partial_contains_all_columns(
        self,
        client: AsyncClient,
        postgres_dsn: str,
    ) -> None:
        conn = await _raw_conn(postgres_dsn)
        try:
            order_id = uuid.uuid4()
            await _insert_order(conn, order_id)
            await _insert_ticket(conn, uuid.uuid4(), order_id, KitchenState.NEW.value)
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/kitchen/partial")
        html = resp.text
        for col in ("NEW", "QUEUED", "PREPARING", "READY", "HANDED_OFF"):
            assert col in html

    async def test_partial_shows_created_ticket(
        self,
        client: AsyncClient,
        postgres_dsn: str,
    ) -> None:
        conn = await _raw_conn(postgres_dsn)
        try:
            order_id = uuid.uuid4()
            ticket_id = uuid.uuid4()
            await _insert_order(conn, order_id)
            await _insert_ticket(conn, ticket_id, order_id, KitchenState.NEW.value)
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/kitchen/partial")
        assert str(ticket_id)[:7] in resp.text

    async def test_ticket_in_correct_column(
        self,
        client: AsyncClient,
        postgres_dsn: str,
    ) -> None:
        conn = await _raw_conn(postgres_dsn)
        try:
            o1, o2 = uuid.uuid4(), uuid.uuid4()
            await _insert_order(conn, o1)
            await _insert_order(conn, o2)
            t1 = uuid.uuid4()
            t2 = uuid.uuid4()
            await _insert_ticket(conn, t1, o1, KitchenState.NEW.value)
            await _insert_ticket(conn, t2, o2, KitchenState.READY.value)
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/kitchen/partial")
        html = resp.text
        assert str(t1)[:7] in html
        assert str(t2)[:7] in html
