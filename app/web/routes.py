"""app/web/routes.py — dashboard UI routes, mounted at /admin/ui/*."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.domains.orders.models import OrderRead, OrderState
from app.services.order_service import OrderService

router = APIRouter(prefix="/admin/ui")

_COLUMN_GROUPING: list[tuple[str, set[OrderState]]] = [
    ("DRAFT", {OrderState.DRAFT}),
    ("CONFIRMED", {OrderState.CONFIRMED, OrderState.PAYMENT_PENDING, OrderState.PAID}),
    ("COOKING", {OrderState.COOKING}),
    ("READY", {OrderState.PACKING, OrderState.COURIER_ASSIGNED, OrderState.DELIVERING}),
    ("DELIVERED", {OrderState.DELIVERED, OrderState.CLOSED}),
    ("CANCELLED", {OrderState.CANCELLED}),
]


def _group_orders(orders: list[OrderRead]) -> dict[str, list[OrderRead]]:
    columns: dict[str, list[OrderRead]] = {name: [] for name, _ in _COLUMN_GROUPING}
    for order in orders:
        for name, states in _COLUMN_GROUPING:
            if order.state in states:
                columns[name].append(order)
                break
    return columns


def get_order_service() -> OrderService:
    return OrderService()


@router.get("/", response_class=Response)
async def dashboard_home(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard_home.html")  # type: ignore[no-any-return]


@router.get("/orders", response_class=Response)
async def orders_board(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "orders_board.html")  # type: ignore[no-any-return]


@router.get("/orders/partial", response_class=Response)
async def orders_board_partial(
    request: Request,
    service: OrderService = Depends(get_order_service),
) -> Response:
    orders = await service.list_orders()
    columns = _group_orders(orders)
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "orders_board_partial.html", {"columns": columns}
    )
