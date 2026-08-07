"""tests/unit/test_inventory_routes.py — GET /inventory/items, POST /inventory/
sync-json, PATCH /inventory/{sku} route wiring. Fake InventoryService via
dependency_overrides — no Postgres/Docker needed, this only checks the route
layer (request/response shape, status codes), not the real DB-backed service
(that's covered by test_inventory_service_set_quantity.py + the integration
tests in tests/integration/test_inventory_panel.py).
sprint_inventory_restock_inline (2026-08)."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.agents.inventory_agent import InventoryAgent, InventoryAgentResult, InventoryApplyResult
from app.domains.inventory.models import InventoryState
from app.services.inventory_service import (
    InventoryItemRead,
    InventorySyncResult,
    MovementsSummary,
)
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
        self.summary_result = MovementsSummary(
            labels=["2026-08-01"], restocked=[10], sold=[3]
        )
        self.csv_result = "sku,delta,reason,source_type,source_id,below_zero,created_at\n"
        self.received_dates: tuple[date | None, date | None] | None = None

    async def movements_summary(
        self, from_date: date | None = None, to_date: date | None = None
    ) -> MovementsSummary:
        self.received_dates = (from_date, to_date)
        return self.summary_result

    async def export_movements_csv(
        self, from_date: date | None = None, to_date: date | None = None
    ) -> str:
        self.received_dates = (from_date, to_date)
        return self.csv_result

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


class TestInventoryRestockRoute:
    """sprint_inventory_restock_agent (2026-08). InventoryAgent is instantiated
    directly inside the route (no DI, same as PromotionAgent/promotion_apply),
    so it's patched at the class level rather than via dependency_overrides."""

    async def test_success(self, client: AsyncClient) -> None:
        with (
            patch.object(
                InventoryAgent, "manage_restock",
                AsyncMock(return_value=InventoryAgentResult(
                    success=True, command={"sku": "coffee", "quantity": 50}
                )),
            ),
            patch.object(
                InventoryAgent, "apply_restock",
                AsyncMock(return_value=InventoryApplyResult(
                    applied=True, result={"applied": True, "sku": "coffee", "quantity": 50},
                    trace_id="t1",
                )),
            ),
        ):
            resp = await client.post(
                "/admin/ui/inventory/restock", json={"instruction": "Добавь 50 на coffee"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["command"] == {"sku": "coffee", "quantity": 50}
        assert data["error"] is None
        assert len(data["items"]) == 1

    async def test_collect_failure(self, client: AsyncClient) -> None:
        with patch.object(
            InventoryAgent, "manage_restock",
            AsyncMock(return_value=InventoryAgentResult(
                success=False, error="could not identify which sku to restock"
            )),
        ):
            resp = await client.post(
                "/admin/ui/inventory/restock", json={"instruction": "gibberish"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["command"] is None
        assert "sku" in data["error"]

    async def test_apply_rejected(self, client: AsyncClient) -> None:
        with (
            patch.object(
                InventoryAgent, "manage_restock",
                AsyncMock(return_value=InventoryAgentResult(
                    success=True, command={"sku": "ghost", "quantity": 5}
                )),
            ),
            patch.object(
                InventoryAgent, "apply_restock",
                AsyncMock(return_value=InventoryApplyResult(
                    applied=False, error="sku 'ghost' not found in inventory",
                )),
            ),
        ):
            resp = await client.post(
                "/admin/ui/inventory/restock", json={"instruction": "Добавь 5 на ghost"}
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["command"] == {"sku": "ghost", "quantity": 5}
        assert "not found" in data["error"]

    async def test_missing_instruction_field_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/admin/ui/inventory/restock", json={})

        assert resp.status_code == 422


class TestInventoryMovementsSummaryRoute:
    """sprint_inventory_stats_viz (2026-08)."""

    async def test_returns_summary_json(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/inventory/movements-summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"labels": ["2026-08-01"], "restocked": [10], "sold": [3]}

    async def test_passes_date_query_params_through(
        self, client: AsyncClient, fake_service: FakeInventoryService,
    ) -> None:
        resp = await client.get(
            "/admin/ui/inventory/movements-summary",
            params={"from_date": "2026-08-01", "to_date": "2026-08-07"},
        )

        assert resp.status_code == 200
        assert fake_service.received_dates == (date(2026, 8, 1), date(2026, 8, 7))

    async def test_no_date_params_passes_none(
        self, client: AsyncClient, fake_service: FakeInventoryService,
    ) -> None:
        resp = await client.get("/admin/ui/inventory/movements-summary")

        assert resp.status_code == 200
        assert fake_service.received_dates == (None, None)

    async def test_invalid_date_returns_422(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/admin/ui/inventory/movements-summary",
            params={"from_date": "not-a-date"},
        )

        assert resp.status_code == 422


class TestInventoryExportRoute:
    """sprint_inventory_stats_viz (2026-08)."""

    async def test_returns_csv_with_attachment_headers(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/ui/inventory/export")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        assert "inventory_movements.csv" in resp.headers["content-disposition"]
        assert resp.text == "sku,delta,reason,source_type,source_id,below_zero,created_at\n"

    async def test_filename_includes_date_range_when_provided(
        self, client: AsyncClient,
    ) -> None:
        resp = await client.get(
            "/admin/ui/inventory/export",
            params={"from_date": "2026-08-01", "to_date": "2026-08-07"},
        )

        assert resp.status_code == 200
        assert "2026-08-01_2026-08-07" in resp.headers["content-disposition"]

    async def test_passes_date_query_params_through(
        self, client: AsyncClient, fake_service: FakeInventoryService,
    ) -> None:
        await client.get(
            "/admin/ui/inventory/export",
            params={"from_date": "2026-08-01"},
        )

        assert fake_service.received_dates == (date(2026, 8, 1), None)
