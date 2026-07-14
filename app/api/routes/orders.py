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


class PaymentRequest(BaseModel):
    amount: Decimal
    currency: str = "RUB"
    return_url: str | None = None


class PaymentInitResponse(BaseModel):
    confirmation_url: str
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
    result = await payment.create_payment(
        order_id=str(order_id),
        amount=body.amount,
        currency=body.currency,
        return_url=body.return_url,
    )
    return PaymentInitResponse(**result)
