"""tests/unit/test_csrf_guard.py — require_same_origin (security_fix_vuln06,
audit P5).

The dashboard's mutating endpoints rely on HTTP Basic auth, which browsers
attach automatically — including on cross-site form POSTs. The guard must
reject mutating requests whose Origin names a different host, and stay out
of the way for same-origin browsers and Origin-less non-browser clients.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient

from app.web.auth import require_same_origin

router = APIRouter(dependencies=[Depends(require_same_origin)])


@router.post("/mutate")
async def mutate() -> dict[str, bool]:
    return {"ok": True}


@router.get("/read")
async def read() -> dict[str, bool]:
    return {"ok": True}


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as ac:
        yield ac


class TestRequireSameOrigin:
    async def test_post_without_origin_passes(self, client: AsyncClient) -> None:
        """curl / server-to-server / existing tests: no Origin, no CSRF check."""
        resp = await client.post("/mutate")
        assert resp.status_code == 200

    async def test_post_with_same_origin_passes(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/mutate",
            headers={"Origin": "http://testserver", "Host": "testserver"},
        )
        assert resp.status_code == 200

    async def test_post_with_cross_origin_rejected(self, client: AsyncClient) -> None:
        """The VULN-06 attack shape: admin's browser auto-submits to us from
        an attacker page — Origin names the attacker's host."""
        resp = await client.post(
            "/mutate",
            headers={"Origin": "https://evil.example", "Host": "testserver"},
        )
        assert resp.status_code == 403

    async def test_post_with_null_origin_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/mutate",
            headers={"Origin": "null", "Host": "testserver"},
        )
        assert resp.status_code == 403

    async def test_get_with_cross_origin_passes(self, client: AsyncClient) -> None:
        """GETs carry no state change — the guard only covers mutating verbs."""
        resp = await client.get(
            "/read",
            headers={"Origin": "https://evil.example", "Host": "testserver"},
        )
        assert resp.status_code == 200
