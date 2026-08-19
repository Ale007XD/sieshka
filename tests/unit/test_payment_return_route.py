"""tests/unit/test_payment_return_route.py — GET /payment/return wiring.

sprint_fix_online_payment_funnel (2026-08-19): settings.YOOKASSA_RETURN_URL
has always pointed at "<domain>/payment/return", but no route existed for
it at all — live symptom was a plain {"detail":"Not Found"} 404 the instant
a customer finished (or bounced off) the YooKassa hosted payment page, with
no way back into the app and no order-status visibility.

checkout.py now appends "?order_id=<uuid>" to the return_url it hands to
PaymentService.create_payment() (see test_checkout_endpoint.py::
test_sbp_path_return_url_carries_order_id), so /payment/return's job is
simply: redirect to the existing /thanks/{order_id} page, which already
renders order state — no new template, no new service call.
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.web.customer_routes import router as customer_router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(customer_router)
    return app


async def test_payment_return_with_order_id_redirects_to_thanks() -> None:
    order_id = str(uuid4())
    app = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(
            f"/payment/return?order_id={order_id}", follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == f"/thanks/{order_id}"


async def test_payment_return_without_order_id_redirects_to_root() -> None:
    app = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get("/payment/return", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


async def test_payment_return_with_malformed_order_id_redirects_to_root() -> None:
    """A malformed order_id (stale bookmark, tampered query string, etc.)
    must not 500 or be forwarded verbatim into /thanks/{order_id} — falls
    back to the storefront root instead of guessing."""
    app = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(
            "/payment/return?order_id=not-a-uuid", follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
