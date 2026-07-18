from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from nano_vm_mcp.store import ProgramStore

from app.agents.schedule_agent import ScheduleAgent
from app.db_nano import get_store as get_nano_store
from app.domains.orders.models import OrderRead, OrderState
from app.services.menu_import_service import ImportReport, MenuImportService
from app.services.order_service import OrderService
from app.services.schedule_service import ScheduleService
from app.services.trace_analyzer import ExecutionReceipt, TraceAnalyzer

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


@router.get("/ui/menu", response_class=HTMLResponse)
async def menu_admin_ui(
    request: Request,
    service: MenuImportService = Depends(get_menu_import_service),
) -> HTMLResponse:
    """Render the menu admin page: product table + upload form + last report."""
    products, counts = await service.get_admin_data()
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "menu_admin.html",
        {
            "products": products,
            "counts": counts,
            "report": _last_menu_import_report,
            "form_action": "/admin/menu/import-csv",
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
