"""tests/integration/test_static_mount.py — static files mount smoke test."""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app


class TestStaticMount:
    async def test_static_placeholder_served_200(self) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/static/css/placeholder.css")
            assert resp.status_code == 200
            assert "placeholder" in resp.text

    async def test_static_dir_trailing_slash_200(self) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/static/")
            assert resp.status_code == 200
