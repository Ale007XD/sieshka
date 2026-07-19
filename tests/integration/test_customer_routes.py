"""tests/integration/test_customer_routes.py — customer storefront routes.

Builds a minimal FastAPI app (templates + customer_router) and overrides the
DB-backed services (schedule / order) with in-memory fakes so the routes can
be exercised without Docker. Mirrors the no-Docker approach of
test_legal_pages.py.

Covers: every storefront route returns 200 and renders the expected template,
and the CSP nonce is present in the rendered HTML (the matching CSP response
header is asserted separately).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.web.csp import CSPMiddleware
from app.web.customer_routes import (
    get_order_service,
    get_schedule_service,
)
from app.web.customer_routes import (
    router as customer_router,
)


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parents[2] / "app" / "web" / "templates")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=_templates_dir())
    app.include_router(customer_router)

    fake_window: dict[str, Any] = {
        "is_open": True,
        "is_evening_preorder": False,
        "morning_start": "09:00:00",
        "morning_end": "16:00:00",
        "evening_start": "16:00:00",
        "evening_end": "21:00:00",
        "preorder_info": None,
    }
    fake_schedule = AsyncMock()
    fake_schedule.get_menu_window_context = AsyncMock(return_value=fake_window)

    fake_order = AsyncMock()
    fake_order.get_order = AsyncMock(return_value=None)

    async def _schedule() -> Any:
        return fake_schedule

    async def _order() -> Any:
        return fake_order

    app.dependency_overrides[get_schedule_service] = _schedule
    app.dependency_overrides[get_order_service] = _order
    app.add_middleware(CSPMiddleware)
    return app


async def _get(path: str) -> Any:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


class TestCustomerRoutes:
    async def test_index_200(self) -> None:
        resp = await _get("/")
        assert resp.status_code == 200
        assert "<title>" in resp.text
        assert "Sieshka" in resp.text

    async def test_menu_200(self) -> None:
        resp = await _get("/menu")
        assert resp.status_code == 200
        assert "Меню" in resp.text

    async def test_cart_200(self) -> None:
        resp = await _get("/cart")
        assert resp.status_code == 200
        assert "Корзина" in resp.text

    async def test_checkout_200(self) -> None:
        resp = await _get("/checkout")
        assert resp.status_code == 200
        assert "Оформление заказа" in resp.text

    async def test_closed_200(self) -> None:
        resp = await _get("/closed")
        assert resp.status_code == 200
        assert "закрыт" in resp.text.lower() or "Закрыто" in resp.text

    async def test_thanks_200_unknown_order(self) -> None:
        resp = await _get("/thanks/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 200
        assert "Спасибо" in resp.text

    async def test_thanks_400_bad_uuid_still_renders(self) -> None:
        resp = await _get("/thanks/not-a-uuid")
        assert resp.status_code == 200
        assert "Спасибо" in resp.text

    async def test_index_csp_nonce_present(self) -> None:
        resp = await _get("/")
        assert resp.status_code == 200
        # The inline <script> tags in the template must carry the nonce.
        assert "nonce=" in resp.text

    async def test_index_csp_header_present(self) -> None:
        resp = await _get("/")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert "script-src" in csp
        assert "nonce-" in csp
        assert "https://yookassa.ru" in csp

        # The nonce in the CSP header must match the nonce in the rendered
        # inline <script> tags (otherwise the browser rejects the scripts).
        import re

        header_nonce = re.search(r"nonce-([A-Za-z0-9_-]+)", csp)
        assert header_nonce is not None
        assert f'nonce="{header_nonce.group(1)}"' in resp.text

    async def test_legal_pages_still_served(self) -> None:
        for path in ("/agreement", "/offer", "/privacy", "/requisites"):
            resp = await _get(path)
            assert resp.status_code == 200
