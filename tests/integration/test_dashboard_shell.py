"""tests/integration/test_dashboard_shell.py — dashboard UI scaffold tests.

No Docker/testcontainers required — pure template rendering.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.web.routes import get_kitchen_service, get_order_service
from app.web.routes import router as web_router

pytestmark: list[object] = []


class _FakeOrderService:
    async def list_orders(self, state_filter: object = None) -> list[object]:
        return []


class _FakeKitchenService:
    async def list_tickets(self) -> list[object]:
        return []


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parents[2] / "app" / "web" / "templates")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=_templates_dir())
    app.include_router(web_router)
    app.dependency_overrides[get_order_service] = lambda: _FakeOrderService()
    app.dependency_overrides[get_kitchen_service] = lambda: _FakeKitchenService()
    return app


class TestDashboardShell:
    async def test_dashboard_home_returns_200(self) -> None:
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/ui/")
            assert resp.status_code == 200

    async def test_dashboard_home_contains_nav_links(self) -> None:
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/ui/")
            html = resp.text

            assert "Dashboard" in html
            assert "Orders" in html
            assert "Kitchen" in html
            assert "Inventory" in html
            assert "Promotions" in html
            assert "Menu" in html
            assert "Zones" in html
            assert "Schedule" in html
            assert "Stats" in html

    async def test_dashboard_home_shows_stage_counts(self) -> None:
        """Regression for the 2026-08-20 nav/dashboard cleanup — dashboard
        used to be a static 'Welcome to the Sieshka admin dashboard.'
        placeholder. It now shows live order/kitchen stage counts reusing
        the same _group_orders/_group_kitchen_tickets grouping the boards
        use, so the numbers can never drift from what /orders and
        /kitchen show."""
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/ui/")
            html = resp.text

            assert "В очереди" in html
            assert "Готовятся" in html
            assert "Активных заказов нет" in html
