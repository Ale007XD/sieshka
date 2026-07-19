"""tests/integration/test_legal_pages.py — public legal pages render.

No Docker/testcontainers required — pure template rendering.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from app.web.customer_routes import router as customer_router

pytestmark: list[object] = []


def _templates_dir() -> str:
    return str(Path(__file__).resolve().parents[2] / "app" / "web" / "templates")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=_templates_dir())
    app.include_router(customer_router)
    return app


class TestLegalPages:
    async def test_agreement_returns_200(self) -> None:
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/agreement")
            assert resp.status_code == 200
            assert "Пользовательское соглашение" in resp.text
            assert "320385000007573" in resp.text

    async def test_offer_returns_200(self) -> None:
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/offer")
            assert resp.status_code == 200
            assert "Публичная оферта" in resp.text
            assert "383101861804" in resp.text

    async def test_privacy_returns_200(self) -> None:
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/privacy")
            assert resp.status_code == 200
            assert "Политика конфиденциальности" in resp.text
            assert "152" in resp.text

    async def test_requisites_returns_200(self) -> None:
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/requisites")
            assert resp.status_code == 200
            assert "Реквизиты" in resp.text
            assert "40802810418350044876" in resp.text
