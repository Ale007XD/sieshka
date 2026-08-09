"""app/api/routes/checkout.py — sprint_m7_checkout_wiring checkout endpoint.

POST /api/orders is the REAL cart.js contract (cart.js posts here, not
/checkout). It is the single customer-facing entry point that:

  1. resolves (find-or-create) the Customer by phone,
  2. snapshots item price/name once via menu_service,
  3. computes the server-authoritative total (never trusts a client total),
  4. persists the order with typed OrderItem rows,
  5. for "yookassa_card": creates an embedded-widget YooKassa payment and
     returns {ok, order_id, confirmation_token};
     for "cash": confirms the order immediately and returns {ok, order_id}.

Idempotency is wired into the EXISTING IdempotencyService using the
client-generated idempotency_key — no second mechanism is introduced.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.db import async_session_factory
from app.domains.orders.models import CheckoutRequest, OrderEvent, OrderState
from app.services.customer_service import CustomerService
from app.services.idempotency import IdempotencyService
from app.services.max_staff_notify import notify_admin_order_state
from app.services.max_webapp_auth import validate_init_data
from app.services.menu_service import MenuService
from app.services.order_service import (
    OrderService,
    compute_checkout_total,
    resolve_checkout_items,
    resolve_promo_effect,
)
from app.services.payment_service import PaymentService
from app.services.zone_service import ZoneService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["checkout"])
promo_router = APIRouter(prefix="/api/promo", tags=["promo"])


class CheckoutResponse(BaseModel):
    ok: bool
    order_id: str
    confirmation_token: str | None = None


class PromoCheckRequest(BaseModel):
    code: str
    subtotal: int


class PromoCheckResponse(BaseModel):
    valid: bool
    discount_rub: int = 0
    free_delivery: bool = False


def get_order_service() -> OrderService:
    return OrderService()


def get_customer_service() -> CustomerService:
    return CustomerService()


def get_menu_service() -> MenuService:
    return MenuService()


def get_payment_service() -> PaymentService:
    return PaymentService()


def get_idempotency_service() -> IdempotencyService:
    return IdempotencyService()


def get_zone_service() -> ZoneService:
    return ZoneService()


_IDEMPOTENCY_PREFIX = "checkout:"


async def _recover_idempotent_result(
    idem_key: str,
    idempotency: IdempotencyService,
) -> CheckoutResponse | None:
    """Reconstruct the original checkout response for a replayed idempotency_key.

    Returns ``None`` only when the duplicate arrived before the first call had
    finished persisting its order_id (a rare in-flight race) — callers then let
    normal re-resolution proceed.
    """
    payload = await idempotency.get_payload(idem_key)
    if payload is None:
        return None
    order_id = payload.get("order_id")
    if not isinstance(order_id, str):
        return None
    token = payload.get("confirmation_token")
    return CheckoutResponse(
        ok=True,
        order_id=order_id,
        confirmation_token=token if isinstance(token, str) and token else None,
    )


@router.post("", status_code=200, response_model=CheckoutResponse)
async def checkout(
    body: CheckoutRequest,
    request: Request,
    order_service: OrderService = Depends(get_order_service),
    customer_service: CustomerService = Depends(get_customer_service),
    menu_service: MenuService = Depends(get_menu_service),
    payment_service: PaymentService = Depends(get_payment_service),
    idempotency: IdempotencyService = Depends(get_idempotency_service),
    zone_service: ZoneService = Depends(get_zone_service),
) -> CheckoutResponse:
    if body.payment_method not in ("yookassa_card", "cash"):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported payment_method: {body.payment_method!r}",
        )
    if body.delivery_mode != "pickup" and body.zone_id is None:
        raise HTTPException(
            status_code=400,
            detail="zone_id is required for non-pickup delivery",
        )
    if not body.items:
        raise HTTPException(status_code=400, detail="order must contain at least one item")

    # sprint_max_storefront: body.client_max_uid is an unverified client
    # claim until this point — a plain int anyone could set in the JSON
    # body regardless of whether they came from MAX at all. Overriding it
    # here (not merging/coalescing) means only a request MAX itself signed
    # can end up with a non-null client_max_uid persisted on the order.
    web_app_data = request.headers.get("X-Max-Init-Data")
    verified_max_uid: int | None = None
    if web_app_data:
        verified_max_uid = validate_init_data(web_app_data, settings.MAX_BOT_TOKEN)
        if verified_max_uid is None:
            logger.warning("checkout: X-Max-Init-Data present but failed validation")
    body.client_max_uid = verified_max_uid

    # Idempotency: key supplied by the client, wired into the existing service.
    idem_key = f"{_IDEMPOTENCY_PREFIX}{body.idempotency_key}"
    inserted = await idempotency.check_and_record(
        idem_key,
        {
            "phone": body.phone,
            "payment_method": body.payment_method,
            "item_count": len(body.items),
        },
    )
    if not inserted:
        # Duplicate request: reuse the previously created order/token.
        existing = await _recover_idempotent_result(idem_key, idempotency)
        if existing is not None:
            return existing
        raise HTTPException(
            status_code=409,
            detail="duplicate idempotency_key still being processed",
        )

    customer = await customer_service.find_or_create_by_phone(body.name, body.phone)
    items = await resolve_checkout_items(body.items, menu_service)
    goods_total = sum(item.price_rub * item.qty for item in items)
    async with async_session_factory() as session:
        promo_effect = await resolve_promo_effect(session, body.promo_code, goods_total)
    zone_fee: int | None = None
    if body.zone_id is not None:
        zone = await zone_service.get_by_id(body.zone_id)
        if zone is not None:
            zone_fee = zone.delivery_fee_rub

    total_rub = compute_checkout_total(
        items, body.delivery_mode, promo_effect, delivery_fee=zone_fee,
    )

    order = await order_service.create_order_from_checkout(
        data=body,
        customer_id=customer.id,
        items=items,
        total_rub=total_rub,
        promo_code=promo_effect.applied_code if promo_effect else None,
        discount_rub=promo_effect.discount_rub if promo_effect else 0,
    )

    if body.payment_method == "yookassa_card":
        # BUGFIX (2026-07-19): this branch created the order and requested a
        # YooKassa payment, but never advanced the order's own FSM state —
        # it stayed DRAFT forever, even on a fully successful payment
        # creation. ORDER_TRANSITIONS requires two hops to get from DRAFT to
        # PAYMENT_PENDING: DRAFT -CONFIRM-> CONFIRMED -REQUEST_PAYMENT->
        # PAYMENT_PENDING (app/domains/orders/models.py). Done BEFORE calling
        # payment_service.create_payment(): if the YooKassa call itself then
        # fails, the order correctly reflects "payment was requested" (safe
        # to retry) rather than silently reporting DRAFT with no record a
        # payment attempt was ever made.
        await order_service.transition_order(str(order.id), OrderEvent.CONFIRM)
        await order_service.transition_order(str(order.id), OrderEvent.REQUEST_PAYMENT)
        await notify_admin_order_state(str(order.id), OrderState.PAYMENT_PENDING)
        payment = await payment_service.create_payment(
            order_id=str(order.id),
            amount=Decimal(total_rub),
            currency="RUB",
            description=f"Order {order.id}",
        )
        confirmation_token = payment.get("confirmation_token", "")
        await idempotency.update_payload(
            idem_key,
            {
                "phone": body.phone,
                "payment_method": body.payment_method,
                "item_count": len(body.items),
                "order_id": str(order.id),
                "confirmation_token": confirmation_token,
            },
        )
        return CheckoutResponse(
            ok=True,
            order_id=str(order.id),
            confirmation_token=confirmation_token or None,
        )

    # Cash: no external payment — confirm the order so the kitchen can proceed.
    await order_service.transition_order(str(order.id), OrderEvent.CONFIRM)
    # notify_admin_order_state fires HERE (state=CONFIRMED), not after
    # START_COOKING below: once START_COOKING succeeds the order is in
    # COOKING, where CANCEL is no longer a valid transition, so the same
    # call made after that point would silently no-op (keyboard-empty
    # short-circuit) — admin would never see the order at all for the
    # common cash-order path (2026-08-08, reported as "ничего не приходит").
    await notify_admin_order_state(str(order.id), OrderState.CONFIRMED)
    cooking = await order_service.transition_order(str(order.id), OrderEvent.START_COOKING)
    if not cooking.success:
        logger.warning(
            "checkout: START_COOKING failed for cash order %s: %s",
            order.id, cooking.reason,
        )
    await idempotency.update_payload(
        idem_key,
        {
            "phone": body.phone,
            "payment_method": body.payment_method,
            "item_count": len(body.items),
            "order_id": str(order.id),
        },
    )
    return CheckoutResponse(ok=True, order_id=str(order.id))


@promo_router.post("/check", response_model=PromoCheckResponse)
async def check_promo_code(body: PromoCheckRequest) -> PromoCheckResponse:
    """Read-only preview for the checkout "Apply" button — does not create or
    mutate anything, just lets the customer see the effect before submitting."""
    async with async_session_factory() as session:
        effect = await resolve_promo_effect(session, body.code, body.subtotal)
    if effect is None:
        return PromoCheckResponse(valid=False)
    return PromoCheckResponse(
        valid=True,
        discount_rub=effect.discount_rub,
        free_delivery=effect.free_delivery,
    )
