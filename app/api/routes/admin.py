from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from nano_vm_mcp.store import ProgramStore

from app.db_nano import get_store as get_nano_store
from app.domains.orders.models import OrderRead, OrderState
from app.services.menu_import_service import ImportReport, MenuImportService
from app.services.order_service import OrderService
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
