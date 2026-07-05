"""app/web/routes.py — dashboard UI routes, mounted at /admin/ui/*."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.domains.kitchen.fsm import KitchenState
from app.domains.orders.models import OrderRead, OrderState
from app.services.inventory_service import InventoryService
from app.services.kitchen_service import KitchenService, KitchenTicketRead
from app.services.order_service import OrderService
from app.services.promotion_service import PromotionService
from app.web.helpers import INVENTORY_STATE_COLOR, PROMOTION_STATE_COLOR

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


_KITCHEN_COLUMNS: list[tuple[str, set[KitchenState]]] = [
    ("NEW", {KitchenState.NEW}),
    ("QUEUED", {KitchenState.QUEUED}),
    ("PREPARING", {KitchenState.PREPARING}),
    ("READY", {KitchenState.READY}),
    ("HANDED_OFF", {KitchenState.HANDED_OFF}),
]


def _group_kitchen_tickets(
    tickets: list[KitchenTicketRead],
) -> dict[str, list[KitchenTicketRead]]:
    columns: dict[str, list[KitchenTicketRead]] = {
        name: [] for name, _ in _KITCHEN_COLUMNS
    }
    for ticket in tickets:
        for name, states in _KITCHEN_COLUMNS:
            if ticket.state in states:
                columns[name].append(ticket)
                break
    return columns


def get_kitchen_service() -> KitchenService:
    return KitchenService()


def get_inventory_service() -> InventoryService:
    return InventoryService()


def get_promotion_service() -> PromotionService:
    return PromotionService()


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


@router.get("/kitchen", response_class=Response)
async def kitchen_board(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "kitchen_board.html")  # type: ignore[no-any-return]


@router.get("/kitchen/partial", response_class=Response)
async def kitchen_board_partial(
    request: Request,
    service: KitchenService = Depends(get_kitchen_service),
) -> Response:
    tickets = await service.list_tickets()
    columns = _group_kitchen_tickets(tickets)
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "kitchen_board_partial.html", {"columns": columns}
    )


@router.get("/inventory", response_class=Response)
async def inventory_panel(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "inventory_panel.html")  # type: ignore[no-any-return]


@router.get("/inventory/partial", response_class=Response)
async def inventory_panel_partial(
    request: Request,
    service: InventoryService = Depends(get_inventory_service),
) -> Response:
    items = await service.list_inventory()
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "inventory_panel_partial.html",
        {"items": items, "INVENTORY_STATE_COLOR": INVENTORY_STATE_COLOR},
    )


@router.get("/promotions", response_class=Response)
async def promotions_panel(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "promotions_panel.html")  # type: ignore[no-any-return]


@router.get("/promotions/partial", response_class=Response)
async def promotions_panel_partial(
    request: Request,
    service: PromotionService = Depends(get_promotion_service),
) -> Response:
    promotions = await service.list_promotions()
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "promotions_panel_partial.html",
        {"promotions": promotions, "PROMOTION_STATE_COLOR": PROMOTION_STATE_COLOR},
    )
