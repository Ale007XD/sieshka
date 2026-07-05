"""tests/integration/test_dashboard_shell.py — dashboard UI scaffold tests.

No Docker/testcontainers required — pure template rendering.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.web.routes import router as web_router

pytestmark: list[object] = []


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parents[2] / "app" / "web" / "templates")


class TestDashboardShell:
    async def test_dashboard_home_returns_200(self) -> None:
        app = FastAPI()
        app.state.templates = Jinja2Templates(directory=_templates_dir())
        app.include_router(web_router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/ui/")
            assert resp.status_code == 200

    async def test_dashboard_home_contains_nav_links(self) -> None:
        app = FastAPI()
        app.state.templates = Jinja2Templates(directory=_templates_dir())
        app.include_router(web_router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/ui/")
            html = resp.text

            assert "Dashboard" in html
            assert "Orders" in html
            assert "Kitchen" in html
            assert "Inventory" in html
            assert "Promotions" in html
            assert "Stats" in html
            assert "Receipts" in html
