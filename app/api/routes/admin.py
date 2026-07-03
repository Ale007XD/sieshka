from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.domains.orders.models import OrderRead, OrderState
from app.services.order_service import OrderService
from app.services.trace_analyzer import ExecutionReceipt, TraceAnalyzer

router = APIRouter(prefix="/admin", tags=["admin"])


def get_order_service() -> OrderService:
    return OrderService()


def get_trace_analyzer() -> TraceAnalyzer:
    return TraceAnalyzer()


@router.get("/orders")
async def list_orders(
    state: OrderState | None = Query(None),
    service: OrderService = Depends(get_order_service),
) -> list[OrderRead]:
    return await service.list_orders(state_filter=state)


@router.get("/orders/{order_id}/receipt")
async def get_order_receipt(
    order_id: UUID,
    service: OrderService = Depends(get_order_service),
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
) -> ExecutionReceipt:
    order = await service.get_order(str(order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.trace_id is None:
        raise HTTPException(status_code=404, detail="No trace for this order")
    return await analyzer.receipt(order.trace_id)
