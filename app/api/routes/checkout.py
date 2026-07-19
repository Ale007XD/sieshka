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

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.domains.orders.models import CheckoutRequest, OrderEvent
from app.services.customer_service import CustomerService
from app.services.idempotency import IdempotencyService
from app.services.menu_service import MenuService
from app.services.order_service import (
    OrderService,
    compute_checkout_total,
    resolve_checkout_items,
)
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/api/orders", tags=["checkout"])


class CheckoutResponse(BaseModel):
    ok: bool
    order_id: str
    confirmation_token: str | None = None


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
    order_service: OrderService = Depends(get_order_service),
    customer_service: CustomerService = Depends(get_customer_service),
    menu_service: MenuService = Depends(get_menu_service),
    payment_service: PaymentService = Depends(get_payment_service),
    idempotency: IdempotencyService = Depends(get_idempotency_service),
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
    total_rub = compute_checkout_total(items, body.delivery_mode)

    order = await order_service.create_order_from_checkout(
        data=body,
        customer_id=customer.id,
        items=items,
        total_rub=total_rub,
    )

    if body.payment_method == "yookassa_card":
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
