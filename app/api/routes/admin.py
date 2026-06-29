from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.domains.orders.models import OrderRead, OrderState
from app.services.order_service import OrderService

router = APIRouter(prefix="/admin", tags=["admin"])


def get_order_service() -> OrderService:
    return OrderService()


@router.get("/orders")
async def list_orders(
    state: OrderState | None = Query(None),
    service: OrderService = Depends(get_order_service),
) -> list[OrderRead]:
    return await service.list_orders(state_filter=state)
