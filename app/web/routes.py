"""app/web/routes.py — dashboard UI routes, mounted at /admin/ui/*."""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

router = APIRouter(prefix="/admin/ui")


@router.get("/", response_class=Response)
async def dashboard_home(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard_home.html")  # type: ignore[no-any-return]
