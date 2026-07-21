from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from nano_vm_mcp.store import ProgramStore

from app.agents.menu_agent import MenuAgent
from app.agents.schedule_agent import ScheduleAgent
from app.agents.zone_agent import ZoneAgent
from app.db_nano import get_store as get_nano_store
from app.domains.orders.models import OrderRead, OrderState
from app.services.menu_import_service import ImportReport, MenuImportService
from app.services.order_service import OrderService
from app.services.schedule_service import ScheduleService
from app.services.trace_analyzer import ExecutionReceipt, TraceAnalyzer
from app.services.zone_service import ZoneService

router = APIRouter(prefix="/admin", tags=["admin"])

# Module-level cache of the most recent import report, so GET /admin/ui/menu
# can display it. The dashboard has a single admin user; a process-global
# "last report" is sufficient (and avoids needing SessionMiddleware wired in).
_last_menu_import_report: ImportReport | None = None


def get_menu_import_service() -> MenuImportService:
    return MenuImportService()


def get_order_service() -> OrderService:
    return OrderService()


def get_trace_analyzer() -> TraceAnalyzer:
    return TraceAnalyzer()


def get_transitions_store() -> ProgramStore:
    return get_nano_store()


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


@router.get("/transitions")
async def list_transitions(
    program_name: str = Query(...),
    model_id: str | None = Query(None),
    store: ProgramStore = Depends(get_transitions_store),
) -> list[dict[str, Any]]:
    return store.get_transitions(program_name, model_id)  # type: ignore[no-any-return]


@router.post("/menu/import-csv")
async def import_menu_csv(
    file: UploadFile,
    service: MenuImportService = Depends(get_menu_import_service),
) -> ImportReport:
    """Multipart CSV upload → governed import Program → ImportReport."""
    global _last_menu_import_report
    file_bytes = await file.read()
    report = await service.import_csv(file_bytes)
    _last_menu_import_report = report
    return report


async def _fetch_categories_ref() -> list[dict[str, Any]]:
    """Lightweight read-only categories list for admin dropdowns.

    TODO: belongs in MenuImportService.list_categories() long-term (parity
    with ZoneService.list_all()) — kept inline here because that service file
    wasn't available for this patch. Read-only, no governance concern.
    """
    from sqlalchemy import text as sql_text

    from app.db import async_session_factory

    async with async_session_factory() as session:
        rows = await session.execute(
            sql_text(
                "SELECT id, name FROM categories WHERE is_active = TRUE ORDER BY name"
            )
        )
        return [
            {"id": str(r._mapping["id"]), "name": r._mapping["name"]}
            for r in rows.fetchall()
        ]


def _product_view(products: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "category_name": p.category_name,
            "price_rub": p.price_rub,
            "is_active": p.is_active,
        }
        for p in products
    ]


@router.post("/menu/categories/apply")
async def menu_category_apply(
    payload: dict[str, Any],
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
) -> dict[str, Any]:
    """Create one category from a structured admin form.

    No LLM collect phase — the admin-submitted form IS the confirmed command
    (same contract MenuAgent.apply_category expects from any collect phase).
    """
    agent = MenuAgent()
    apply = await agent.apply_category(payload)
    categories = await _fetch_categories_ref()
    receipt = None
    if apply.trace_id is not None:
        receipt = await analyzer.receipt(apply.trace_id)
    return {
        "ok": apply.applied,
        "error": apply.error,
        "result": apply.result,
        "receipt": receipt.model_dump() if receipt is not None else None,
        "categories": categories,
    }


@router.post("/menu/products/apply")
async def menu_product_apply(
    payload: dict[str, Any],
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
    service: MenuImportService = Depends(get_menu_import_service),
) -> dict[str, Any]:
    """Create one product from a structured admin form. Same no-LLM-collect
    contract as menu_category_apply above."""
    agent = MenuAgent()
    apply = await agent.apply_menu(payload)
    products, counts = await service.get_admin_data()
    receipt = None
    if apply.trace_id is not None:
        receipt = await analyzer.receipt(apply.trace_id)
    return {
        "ok": apply.applied,
        "error": apply.error,
        "result": apply.result,
        "receipt": receipt.model_dump() if receipt is not None else None,
        "products": _product_view(products),
        "counts": counts.model_dump(),
    }


@router.patch("/menu/products/{product_id}/apply")
async def menu_product_update(
    product_id: str,
    payload: dict[str, Any],
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
    service: MenuImportService = Depends(get_menu_import_service),
) -> dict[str, Any]:
    """Update an existing product via the governed update Program.

    product_id is injected from the URL path into the command so the form
    payload doesn't need to carry it (and can't spoof a different id).
    """
    command = {**payload, "product_id": product_id}
    agent = MenuAgent()
    apply = await agent.update_product(command)
    products, counts = await service.get_admin_data()
    receipt = None
    if apply.trace_id is not None:
        receipt = await analyzer.receipt(apply.trace_id)
    return {
        "ok": apply.applied,
        "error": apply.error,
        "result": apply.result,
        "receipt": receipt.model_dump() if receipt is not None else None,
        "products": _product_view(products),
        "counts": counts.model_dump(),
    }


@router.get("/ui/menu", response_class=HTMLResponse)
async def menu_admin_ui(
    request: Request,
    service: MenuImportService = Depends(get_menu_import_service),
) -> HTMLResponse:
    """Render the menu admin page: product table + upload form + last report."""
    products, counts = await service.get_admin_data()
    categories = await _fetch_categories_ref()
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "menu_admin.html",
        {
            "products": _product_view(products),
            "counts": counts.model_dump(),
            "categories": categories,
            "report": _last_menu_import_report,
            "form_action": "/admin/menu/import-csv",
            "category_form_action": "/admin/menu/categories/apply",
            "product_form_action": "/admin/menu/products/apply",
        },
    )


def get_schedule_service() -> ScheduleService:
    return ScheduleService()


@router.get("/ui/schedule", response_class=HTMLResponse)
async def schedule_admin_ui(
    request: Request,
    service: ScheduleService = Depends(get_schedule_service),
) -> HTMLResponse:
    """Render the schedule admin page: free-text instruction box + window display.

    Shows BOTH the permanent default AND today's override if one is active for
    either period — an admin looking at a permanent-only view while a
    today-override is silently in effect would be misled about what customers
    currently see.
    """
    windows = await service.get_admin_windows()
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "schedule_admin.html",
        {
            "windows": windows,
            "form_action": "/admin/schedule/apply",
        },
    )


@router.post("/schedule/apply")
async def schedule_apply(
    payload: dict[str, Any],
    request: Request,
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
) -> dict[str, Any]:
    """Run a free-text schedule instruction through the ScheduleAgent end-to-end.

    Collect phase (LLM parse) → apply phase (governed write). Returns the
    confirmed command, the ExecutionReceipt, and the current window display.
    """
    instruction = payload.get("instruction", "")
    agent = ScheduleAgent()
    collect = await agent.collect_schedule({"input_text": instruction})
    if not collect.success or collect.command is None:
        windows = await ScheduleService().get_admin_windows()
        return {
            "ok": False,
            "error": collect.error or "unparseable instruction",
            "command": None,
            "receipt": None,
            "windows": windows,
        }

    apply = await agent.apply_schedule(collect.command)
    windows = await ScheduleService().get_admin_windows()
    receipt = None
    if apply.trace_id is not None:
        receipt = await analyzer.receipt(apply.trace_id)
    return {
        "ok": apply.applied,
        "error": apply.error,
        "command": collect.command,
        "receipt": receipt.model_dump() if receipt is not None else None,
        "windows": windows,
    }


def get_zone_service() -> ZoneService:
    return ZoneService()


@router.get("/ui/zones", response_class=HTMLResponse)
async def zones_admin_ui(
    request: Request,
    service: ZoneService = Depends(get_zone_service),
) -> HTMLResponse:
    """Render the zone admin page: free-text instruction box + zone reference.

    A single chat-style instruction input (NOT a structured create/edit form)
    that runs through ZoneAgent end-to-end, plus a read-only table of ALL zones
    (active + retired) for reference.
    """
    zones = await service.list_all()
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "zones_admin.html",
        {
            "zones": zones,
            "form_action": "/admin/zones/apply",
        },
    )


@router.post("/zones/apply")
async def zone_apply(
    payload: dict[str, Any],
    request: Request,
    analyzer: TraceAnalyzer = Depends(get_trace_analyzer),
) -> dict[str, Any]:
    """Run a free-text zone instruction through the ZoneAgent end-to-end.

    Collect phase (LLM parse) -> apply phase (governed write). Returns the
    confirmed command, the ExecutionReceipt, and the current zone reference list.
    """
    instruction = payload.get("instruction", "")
    agent = ZoneAgent()
    collect = await agent.collect_zone({"input_text": instruction})
    if not collect.success or collect.command is None:
        zones = await ZoneService().list_all()
        return {
            "ok": False,
            "error": collect.error or "unparseable instruction",
            "command": None,
            "receipt": None,
            "zones": _zone_view(zones),
        }

    apply = await agent.apply_zone(collect.command)
    zones = await ZoneService().list_all()
    receipt = None
    if apply.trace_id is not None:
        receipt = await analyzer.receipt(apply.trace_id)
    return {
        "ok": apply.applied,
        "error": apply.error,
        "command": collect.command,
        "receipt": receipt.model_dump() if receipt is not None else None,
        "zones": _zone_view(zones),
    }


def _zone_view(zones: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(z.id),
            "name": z.name,
            "delivery_time_minutes": z.delivery_time_minutes,
            "is_active": z.is_active,
        }
        for z in zones
    ]
