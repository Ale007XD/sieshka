"""app/web/customer_routes.py — public customer-facing legal pages.

Pure content templates, no backend logic beyond rendering.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

router = APIRouter()

_LEGAL_PAGES: dict[str, str] = {
    "agreement": "customer/agreement.html",
    "offer": "customer/offer.html",
    "privacy": "customer/privacy.html",
    "requisites": "customer/requisites.html",
}


@router.get("/agreement", response_class=Response)
async def agreement(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LEGAL_PAGES["agreement"])  # type: ignore[no-any-return]


@router.get("/offer", response_class=Response)
async def offer(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LEGAL_PAGES["offer"])  # type: ignore[no-any-return]


@router.get("/privacy", response_class=Response)
async def privacy(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LEGAL_PAGES["privacy"])  # type: ignore[no-any-return]


@router.get("/requisites", response_class=Response)
async def requisites(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LEGAL_PAGES["requisites"])  # type: ignore[no-any-return]
