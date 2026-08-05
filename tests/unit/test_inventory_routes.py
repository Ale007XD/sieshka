"""tests/unit/test_inventory_routes.py — GET /inventory/items, POST /inventory/
sync-json, PATCH /inventory/{sku} route wiring. Fake InventoryService via
dependency_overrides — no Postgres/Docker needed, this only checks the route
layer (request/response shape, status codes), not the real DB-backed service
(that's covered by test_inventory_service_set_quantity.py + the integration
tests in tests/integration/test_inventory_panel.py).
sprint_inventory_restock_inline (2026-08)."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.domains.inventory.models import InventoryState
from app.services.inventory_service import InventoryItemRead, InventorySyncResult
from app.web.routes import get_inventory_service
from app.web.routes import router as web_router


class FakeInventoryService:
    def __init__(self) -> None:
        self.items = [
            InventoryItemRead(
                sku="coffee", name="Coffee", quantity=10, state=InventoryState.AVAILABLE
            ),
        ]
        self.sync_result = InventorySyncResult(created=0, skipped_no_sku=0)
        self.set_quantity_error: Exception | None = None

    async def list_inventory(self) -> list[InventoryItemRead]:
        return self.items

    async def sync_from_menu(self) -> InventorySyncResult:
        return self.sync_result

    async def set_quantity(self, sku: str, quantity: int) -> InventoryItemRead:
        if self.set_quantity_error is not None:
            raise self.set_quantity_error
        for i, item in enumerate(self.items):
            if item.sku == sku:
                updated = InventoryItemRead(
                    sku=sku, name=item.name, quantity=quantity, state=item.state
                )
                self.items[i] = updated
                return updated
        raise ValueError(f"sku not found: {sku}")


@pytest.fixture
def fake_service() -> FakeInventoryService:
    return FakeInventoryService()


@pytest.fixture
async def client(fake_service: FakeInventoryService) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory="app/web/templates")
    app.include_router(web_router)
    app.dependency_overrides[get_inventory_service] = lambda: fake_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestInventoryItemsRoute:
    async def test_returns_items_as_json(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/inventory/items")

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == [
            {"sku": "coffee", "name": "Coffee", "quantity": 10, "state": "AVAILABLE"}
        ]


class TestInventorySyncJsonRoute:
    async def test_returns_sync_counts_and_items(
        self, client: AsyncClient, fake_service: FakeInventoryService,
    ) -> None:
        fake_service.sync_result = InventorySyncResult(created=3, skipped_no_sku=2)

        resp = await client.post("/admin/ui/inventory/sync-json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 3
        assert data["skipped_no_sku"] == 2
        assert len(data["items"]) == 1


class TestInventorySetQuantityRoute:
    async def test_success_returns_updated_item(self, client: AsyncClient) -> None:
        resp = await client.patch("/admin/ui/inventory/coffee", json={"quantity": 42})

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"] == {
            "sku": "coffee", "name": "Coffee", "quantity": 42, "state": "AVAILABLE"
        }

    async def test_sku_not_found_returns_404(
        self, client: AsyncClient, fake_service: FakeInventoryService,
    ) -> None:
        fake_service.set_quantity_error = ValueError("sku not found: ghost")

        resp = await client.patch("/admin/ui/inventory/ghost", json={"quantity": 5})

        assert resp.status_code == 404

    async def test_missing_quantity_field_returns_422(self, client: AsyncClient) -> None:
        resp = await client.patch("/admin/ui/inventory/coffee", json={})

        assert resp.status_code == 422

    async def test_non_integer_quantity_returns_422(self, client: AsyncClient) -> None:
        resp = await client.patch("/admin/ui/inventory/coffee", json={"quantity": "abc"})

        assert resp.status_code == 422
