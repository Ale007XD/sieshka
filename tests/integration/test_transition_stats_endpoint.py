"""tests/integration/test_transition_stats_endpoint.py — GET /admin/transitions.

Requirements:
  - GET /admin/transitions?program_name=... returns transition stats from nano-vm SQLite store
  - GET /admin/transitions?program_name=...&model_id=... filters by model_id
  - The endpoint MUST NOT write to transition_stats (read-only)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from nano_vm_mcp.store import ProgramStore

from app.api.routes.admin import router as admin_router


@pytest.fixture
def nano_store(tmp_path: Path) -> Generator[ProgramStore, None, None]:
    db_path = tmp_path / "test_transitions.db"
    store = ProgramStore(str(db_path))
    store.upsert_transition("test_prog", "created", "cooking", "gpt-4")
    store.upsert_transition("test_prog", "cooking", "delivering", "gpt-4")
    store.upsert_transition("test_prog", "created", "cancelled", "gpt-3.5")
    store.upsert_transition("other_prog", "created", "shipped", "gpt-4")
    yield store


@pytest.fixture
async def client(
    nano_store: ProgramStore,
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(admin_router)

    from app.api.routes.admin import get_transitions_store

    app.dependency_overrides[get_transitions_store] = lambda: nano_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestTransitionsEndpoint:
    async def test_list_transitions_by_program(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/transitions", params={"program_name": "test_prog"})
        assert resp.status_code == 200
        data: list[dict[str, Any]] = resp.json()
        assert len(data) == 3

        rows = {(r["from_step"], r["to_step"], r["model_id"]): r for r in data}
        assert ("created", "cooking", "gpt-4") in rows
        assert ("cooking", "delivering", "gpt-4") in rows
        assert ("created", "cancelled", "gpt-3.5") in rows
        for r in data:
            assert r["program_name"] == "test_prog"
            assert isinstance(r["count"], int)
            assert isinstance(r["updated_at"], str)

    async def test_list_transitions_filter_by_model(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/admin/transitions",
            params={"program_name": "test_prog", "model_id": "gpt-4"},
        )
        assert resp.status_code == 200
        data: list[dict[str, Any]] = resp.json()
        assert len(data) == 2
        for r in data:
            assert r["model_id"] == "gpt-4"

    async def test_list_transitions_other_program(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/transitions", params={"program_name": "other_prog"})
        assert resp.status_code == 200
        data: list[dict[str, Any]] = resp.json()
        assert len(data) == 1
        assert data[0]["from_step"] == "created"
        assert data[0]["to_step"] == "shipped"

    async def test_list_transitions_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/transitions", params={"program_name": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_transitions_missing_program_name(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/transitions")
        assert resp.status_code == 422

    async def test_list_transitions_read_only(self, client: AsyncClient) -> None:
        """Verify the endpoint does NOT write to transition_stats."""
        resp = await client.get("/admin/transitions", params={"program_name": "other_prog"})
        assert resp.status_code == 200
        data_before: list[dict[str, Any]] = resp.json()

        resp = await client.get("/admin/transitions", params={"program_name": "test_prog"})
        assert resp.status_code == 200

        resp = await client.get("/admin/transitions", params={"program_name": "other_prog"})
        assert resp.status_code == 200
        data_after: list[dict[str, Any]] = resp.json()

        assert data_before == data_after
