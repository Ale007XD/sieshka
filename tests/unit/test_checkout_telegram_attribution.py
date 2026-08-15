"""tests/unit/test_checkout_telegram_attribution.py — client_telegram_uid
verification branch in POST /api/orders (sprint_telegram_miniapp_auth).

Mirrors test_checkout_zalo_attribution.py's fully-mocked pattern (no DB, no
Docker). Unlike Zalo's live-API branch, this exercises a real signed
initData string (offline HMAC, same chain as test_max_webapp_auth.py) since
validate_init_data() is pure computation — no client mock to patch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.checkout import (
    get_customer_service,
    get_idempotency_service,
    get_menu_service,
    get_order_service,
    get_payment_service,
    get_zone_service,
)
from app.api.routes.checkout import router as checkout_router
from app.config import settings
from app.domains.customer.models import Customer
from app.domains.delivery.zones import DeliveryZone
from app.domains.menu.models import MenuProductItem
from app.domains.orders.models import OrderRead, OrderState

_BOT_TOKEN = "test-telegram-bot-token"


def _build_init_data(*, bot_token: str = _BOT_TOKEN, user_id: int = 42) -> str:
    user_json = json.dumps({"id": user_id, "first_name": "Test"})
    params = {"user": user_json, "auth_date": str(int(time.time()))}
    launch_params = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, launch_params.encode(), hashlib.sha256).hexdigest()
    encoded = [f"{k}={quote(v, safe='')}" for k, v in params.items()]
    encoded.append(f"hash={signature}")
    return "&".join(encoded)


def _product_snapshot(pid):
    return MenuProductItem(
        product_id=pid,
        name="Item",
        price_rub=100,
        available=True,
        cta_type="add_to_cart",
        reason_code=None,
    )


def _build_app() -> tuple[FastAPI, dict]:
    app = FastAPI()
    app.include_router(checkout_router)

    order_svc = AsyncMock()
    customer_svc = AsyncMock()
    menu_svc = AsyncMock()
    menu_svc.get_product_snapshot = AsyncMock(side_effect=_product_snapshot)
    payment_svc = AsyncMock()
    idem_svc = AsyncMock()
    idem_svc.check_and_record = AsyncMock(return_value=True)
    zone_svc = AsyncMock()
    zone_svc.get_by_id = AsyncMock(
        return_value=DeliveryZone(
            id=uuid4(),
            external_id=None,
            name="Test Zone",
            delivery_time_minutes=30,
            is_active=True,
            delivery_fee_rub=99,
        )
    )

    app.dependency_overrides[get_order_service] = lambda: order_svc
    app.dependency_overrides[get_customer_service] = lambda: customer_svc
    app.dependency_overrides[get_menu_service] = lambda: menu_svc
    app.dependency_overrides[get_payment_service] = lambda: payment_svc
    app.dependency_overrides[get_idempotency_service] = lambda: idem_svc
    app.dependency_overrides[get_zone_service] = lambda: zone_svc

    customer_svc.find_or_create_by_phone = AsyncMock(
        return_value=Customer(id=uuid4(), name="Ivan", phone="+79991234567")
    )
    created = OrderRead(
        id=uuid4(),
        customer_id=uuid4(),
        state=OrderState.DRAFT,
        items=[],
        delivery_address="Moscow",
    )
    order_svc.create_order_from_checkout = AsyncMock(return_value=created)
    order_svc.transition_order = AsyncMock()

    return app, {"order": order_svc}


def _body(**over) -> dict:
    base = {
        "name": "Ivan",
        "phone": "+79991234567",
        "address": "Moscow",
        "comment": None,
        "delivery_mode": "pickup",
        "delivery_slot": None,
        "delivery_date": None,
        "payment_method": "cash",
        "zone_id": None,
        "items": [{"product_id": str(uuid4()), "qty": 2}],
        "idempotency_key": str(uuid4()),
    }
    base.update(over)
    return base


class TestChecktoutTelegramAttribution:
    async def test_no_header_leaves_client_telegram_uid_none(self) -> None:
        app, svc = _build_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/orders", json=_body())

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_telegram_uid is None

    async def test_valid_init_data_sets_verified_client_telegram_uid(self) -> None:
        app, svc = _build_app()
        data = _build_init_data(user_id=777)

        with patch.object(settings, "TELEGRAM_BOT_TOKEN", _BOT_TOKEN):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/orders",
                    json=_body(),
                    headers={"X-Telegram-Init-Data": data},
                )

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_telegram_uid == 777

    async def test_a_client_supplied_client_telegram_uid_is_overridden_not_trusted(
        self,
    ) -> None:
        """Same non-trust posture as client_max_uid/client_zalo_uid: a
        client-submitted value in the JSON body must never survive
        unverified."""
        app, svc = _build_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/orders", json=_body(client_telegram_uid=987654321)
            )

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_telegram_uid is None

    async def test_invalid_signature_degrades_to_none_order_still_created(self) -> None:
        app, svc = _build_app()

        with patch.object(settings, "TELEGRAM_BOT_TOKEN", _BOT_TOKEN):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/orders",
                    json=_body(),
                    headers={"X-Telegram-Init-Data": "user=%7B%7D&hash=deadbeef"},
                )

        assert resp.status_code == 200
        sent_data = svc["order"].create_order_from_checkout.call_args.kwargs["data"]
        assert sent_data.client_telegram_uid is None
