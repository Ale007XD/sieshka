from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends

from app.domains.orders.models import OrderCreate, OrderEvent, OrderRead
from app.fsm.core.base import TransitionResult
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


def get_order_service() -> OrderService:
    return OrderService()


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
