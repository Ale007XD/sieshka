from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.domains.orders.models import OrderCreate, OrderEvent, OrderRead, OrderState
from app.fsm.core.base import TransitionResult
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/orders", tags=["orders"])


# sprint_pay_endpoint_curation_fix (2026-08-27): this endpoint used to call
# PaymentService.create_payment() with neither confirmation_type nor
# payment_method_data set, which meant it silently fell back to
# YooKassaClient's own confirmation_type="embedded" default with no method
# restriction — the exact combination (embedded widget + unrestricted
# picker) that sprint_yookassa_manual_integration (2026-08-19) removed from
# checkout.py specifically because it renders bank_card and reintroduces the
# MTS Bank ACS 3DS failure inside Mini App WebViews. This endpoint has no
# known live caller (0 references in app/web/static/js or templates), but it
# is mounted with no auth (app/main.py::app.include_router(orders_router)) so
# it is reachable by anyone who knows an order_id. payment_method is now
# required and restricted to the same curated set checkout.py uses, and the
# call is forced onto confirmation_type="redirect" — mirrors checkout.py's
# payment_method → payment_method_data.type mapping instead of duplicating
# YooKassaClient's raw default.
_ALLOWED_PAY_METHODS = ("yookassa_sbp", "yookassa_sberbank")


class PaymentRequest(BaseModel):
    amount: Decimal
    currency: str = "RUB"
    return_url: str | None = None
    payment_method: str = "yookassa_sbp"


class PaymentInitResponse(BaseModel):
    confirmation_url: str
    confirmation_token: str | None = None
    payment_id: str
    trace_id: str


def get_order_service() -> OrderService:
    return OrderService()


def get_payment_service() -> PaymentService:
    return PaymentService()


@router.post("", status_code=201)
async def create_order(
    body: OrderCreate,
    service: OrderService = Depends(get_order_service),
) -> OrderRead:
    return await service.create_order(body)


@router.post("/{order_id}/events")
async def trigger_event(
    order_id: UUID,
    body: OrderEvent = Body(...),
    service: OrderService = Depends(get_order_service),
) -> TransitionResult:
    return await service.transition_order(str(order_id), body)


@router.post("/{order_id}/pay", response_model=PaymentInitResponse, status_code=200)
async def pay_order(
    order_id: UUID,
    body: PaymentRequest,
    service: OrderService = Depends(get_order_service),
    payment: PaymentService = Depends(get_payment_service),
) -> PaymentInitResponse:
    order = await service.get_order(str(order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.state != OrderState.CONFIRMED:
        raise HTTPException(
            status_code=409,
            detail=f"Order must be CONFIRMED to pay; current state: {order.state.value}",
        )
    if body.payment_method not in _ALLOWED_PAY_METHODS:
        raise HTTPException(
            status_code=400,
            detail=f"payment_method must be one of {_ALLOWED_PAY_METHODS}, "
            f"got {body.payment_method!r}",
        )
    result = await payment.create_payment(
        order_id=str(order_id),
        amount=body.amount,
        currency=body.currency,
        return_url=body.return_url,
        customer_phone=order.customer_phone,
        confirmation_type="redirect",
        payment_method_data={
            "type": "sbp" if body.payment_method == "yookassa_sbp" else "sberbank",
        },
    )
    return PaymentInitResponse(
        confirmation_url=result["confirmation_url"],
        confirmation_token=result.get("confirmation_token") or None,
        payment_id=result["payment_id"],
        trace_id=result["trace_id"],
    )
