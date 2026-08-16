"""app/web/telegram_routes.py — Telegram Mini App staff panel page
(sprint_telegram_miniapp_frontend).

Deliberately NOT behind the dashboard's HTTP Basic auth dependency
(app.web.routes / admin_router in main.py) — a Basic auth prompt inside a
Telegram webview is broken UX and irrelevant here anyway: real
authorization happens per-request against app.api.routes.telegram_miniapp
via app.web.telegram_auth.get_current_telegram_staff (X-Telegram-Init-Data),
not against this page load. The page itself carries no sensitive data; every
action button it renders re-proves staff identity server-side on click.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.web.csp import csp_nonce

router = APIRouter(prefix="/telegram", tags=["telegram-miniapp"])


@router.get("/staff", response_class=Response)
async def telegram_staff_panel(
    request: Request,
    nonce: str = Depends(csp_nonce),
) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "telegram_staff.html", {"csp_nonce": nonce}
    )
