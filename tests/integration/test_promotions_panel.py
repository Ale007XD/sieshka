"""tests/integration/test_promotions_panel.py — promotions panel tests.

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

from app.domains.promotions.models import PromotionState
from app.services.promotion_service import PromotionService
from app.web.helpers import PROMOTION_STATE_COLOR
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

    def test_promotion_state_color_has_all_states(self) -> None:
        for state in PromotionState:
            assert state in PROMOTION_STATE_COLOR

    def test_promotion_service_is_callable(self) -> None:
        assert callable(PromotionService)

    def test_helpers_import_from_promotion_models(self) -> None:
        import app.web.helpers as helpers_module

        src = Path(helpers_module.__file__).read_text()
        assert "from app.domains.promotions.models import PromotionState" in src


docker_available = _is_docker_available()


PROMOTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS promotions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    discount    NUMERIC(5,2) NOT NULL,
    state       VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


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
        await conn.execute(PROMOTIONS_SCHEMA)
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

    from app.web.routes import get_promotion_service as web_get_service

    async def _test_service() -> PromotionService:
        return PromotionService(session_factory=session_factory)

    app.dependency_overrides[web_get_service] = _test_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.skipif(not docker_available, reason="Docker required for testcontainers")
class TestPromotionsPanel:
    async def test_partial_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/promotions/partial")
        assert resp.status_code == 200

    async def test_partial_shows_empty_message(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/promotions/partial")
        assert "No promotions found" in resp.text

    async def test_partial_shows_seeded_promotion(self, client: AsyncClient) -> None:
        conn = await asyncpg.connect(
            "postgresql://sieshka:sieshka@localhost:5432/sieshka"
        )
        try:
            await conn.execute(
                "INSERT INTO promotions (name, discount, state) "
                "VALUES ($1, $2, $3)",
                "Summer Sale", 15.0, PromotionState.ACTIVE.value,
            )
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/promotions/partial")
        assert "Summer Sale" in resp.text
        assert "15.0%" in resp.text or "15%" in resp.text
        assert PromotionState.ACTIVE.value in resp.text

    async def test_active_promotion_has_green_class(self, client: AsyncClient) -> None:
        conn = await asyncpg.connect(
            "postgresql://sieshka:sieshka@localhost:5432/sieshka"
        )
        try:
            await conn.execute(
                "INSERT INTO promotions (name, discount, state) "
                "VALUES ($1, $2, $3)",
                "Weekend Deal", 20.0, PromotionState.ACTIVE.value,
            )
        finally:
            await conn.close()

        resp = await client.get("/admin/ui/promotions/partial")
        assert "text-green-600" in resp.text
