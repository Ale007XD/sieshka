"""tests/unit/test_telegram_routes.py — Telegram Mini App staff panel page
route (sprint_telegram_miniapp_frontend).

Mirrors test_dashboard_auth.py's app-fixture pattern (real Jinja2Templates
against the actual templates dir, no mocking of rendering). Unlike
test_dashboard_auth.py, this route is explicitly NOT behind
get_current_username — the assertion here is the inverse: no credentials
required to load the page (see app/web/telegram_routes.py docstring for why).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.web.telegram_routes import router as telegram_page_router


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parents[2] / "app" / "web" / "templates")


@pytest.fixture
def app() -> FastAPI:
    _app = FastAPI()
    _app.state.templates = Jinja2Templates(directory=_templates_dir())
    _app.include_router(telegram_page_router)
    return _app


class TestTelegramStaffPanel:
    async def test_loads_without_credentials(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/telegram/staff")

        assert resp.status_code == 200
        assert "www-authenticate" not in {k.lower() for k in resp.headers}

    async def test_renders_telegram_bridge_script(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/telegram/staff")

        assert "telegram-web-app.js" in resp.text

    async def test_renders_kitchen_and_order_event_vocabulary(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/telegram/staff")

        for ev in ("START_PREP", "MARK_READY", "HAND_OFF"):
            assert ev in resp.text
        for ev in ("ASSIGN_COURIER", "PICKUP", "CANCEL"):
            assert ev in resp.text

    async def test_action_endpoints_referenced_match_telegram_miniapp_api(
        self, app: FastAPI
    ) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/telegram/staff")

        assert "/api/telegram/kitchen/" in resp.text
        assert "/api/telegram/orders/" in resp.text
        assert "X-Telegram-Init-Data" in resp.text
