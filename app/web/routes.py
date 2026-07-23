"""app/web/routes.py — dashboard UI routes, mounted at /admin/ui/*."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from nano_vm_mcp.store import ProgramStore
from starlette.responses import Response

from app.api.routes.admin import get_transitions_store
from app.domains.kitchen.fsm import KitchenState
from app.domains.orders.models import OrderRead, OrderState
from app.programs.order_programs import EVENT_PROGRAM_MAP
from app.services.inventory_service import InventoryService
from app.services.kitchen_service import KitchenService, KitchenTicketRead
from app.services.order_service import OrderService
from app.services.promotion_service import PromotionService
from app.services.trace_analyzer import TraceAnalyzer
from app.web.helpers import INVENTORY_STATE_COLOR, PROMOTION_STATE_COLOR

router = APIRouter(prefix="/admin/ui")

_COLUMN_GROUPING: list[tuple[str, set[OrderState]]] = [
    ("Новые", {OrderState.DRAFT}),
    ("Подтверждены", {OrderState.CONFIRMED, OrderState.PAYMENT_PENDING, OrderState.PAID}),
    ("Готовятся", {OrderState.COOKING}),
    ("Готовы", {OrderState.PACKING, OrderState.COURIER_ASSIGNED, OrderState.DELIVERING}),
    ("Выданы", {OrderState.DELIVERED, OrderState.CLOSED}),
    ("Отменены", {OrderState.CANCELLED}),
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
    ("Новые", {KitchenState.NEW}),
    ("В очереди", {KitchenState.QUEUED}),
    ("Готовятся", {KitchenState.PREPARING}),
    ("Готово", {KitchenState.READY}),
    ("Выдано", {KitchenState.HANDED_OFF}),
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


def get_trace_analyzer() -> TraceAnalyzer:
    return TraceAnalyzer()


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


@router.post("/kitchen/tickets/{ticket_id}/events/{event}", response_class=Response)
async def kitchen_ticket_event(
    request: Request,
    ticket_id: UUID,
    event: str,
    service: KitchenService = Depends(get_kitchen_service),
) -> Response:
    from app.domains.kitchen.fsm import KitchenEvent
    await service.transition_ticket(str(ticket_id), KitchenEvent(event))
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


@router.get("/stats", response_class=Response)
async def stats_dashboard(
    request: Request,
    service: OrderService = Depends(get_order_service),
    store: ProgramStore = Depends(get_transitions_store),
) -> Response:
    orders = await service.list_orders()
    raw_counts: dict[str, int] = {}
    for order in orders:
        raw_counts[order.state.value] = raw_counts.get(order.state.value, 0) + 1

    state_counts: list[dict[str, object]] = []
    for state in OrderState:
        state_counts.append({"state": state.value, "count": raw_counts.get(state.value, 0)})

    all_transitions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for p in EVENT_PROGRAM_MAP.values():
        for t in store.get_transitions(p.name):
            key = (t["program_name"], t["from_step"], t["to_step"], t["model_id"])
            if key not in seen:
                seen.add(key)
                all_transitions.append(t)

    max_count = max((t["count"] for t in all_transitions), default=0)

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "stats_dashboard.html",
        {
            "state_counts": state_counts,
            "transitions": all_transitions,
            "max_transition_count": max_count,
        },
    )


@router.get("/orders/{order_id}/receipt", response_class=Response)
async def order_receipt_viewer(
    request: Request,
    order_id: UUID,
    service: OrderService = Depends(get_order_service),
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
) -> Response:
    order = await service.get_order(str(order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.trace_id is None:
        raise HTTPException(status_code=404, detail="No trace for this order")
    receipt = await analyzer.receipt(order.trace_id)
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "receipt_viewer.html", {"receipt": receipt}
    )
