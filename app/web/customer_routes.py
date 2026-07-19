"""app/web/customer_routes.py — public customer-facing shopfront + legal pages.

Mounted at root ("/"). The shopfront (index / cart / checkout / thanks /
closed) renders the customer templates under app/web/templates/customer/; the
legal pages (agreement / offer / privacy / requisites) render the existing
customer/ legal templates.

The root "/" previously served a JSON status blob — that has been moved to
GET /api/status (see app/main.py) to free the root for the storefront. The
Docker HEALTHCHECK's GET /health is untouched.

Every shopfront template receives ``csp_nonce`` (from the csp_nonce
dependency) and is served with a matching Content-Security-Policy header via
CSPMiddleware (installed in app/main.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.config import settings
from app.services.order_service import OrderService
from app.services.schedule_service import ScheduleService
from app.web.csp import csp_nonce

router = APIRouter()

_LegalPages: dict[str, str] = {
    "agreement": "customer/agreement.html",
    "offer": "customer/offer.html",
    "privacy": "customer/privacy.html",
    "requisites": "customer/requisites.html",
}


def get_order_service() -> OrderService:
    return OrderService()


def get_schedule_service() -> ScheduleService:
    return ScheduleService()


@router.get("/agreement", response_class=Response)
async def agreement(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LegalPages["agreement"])  # type: ignore[no-any-return]


@router.get("/offer", response_class=Response)
async def offer(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LegalPages["offer"])  # type: ignore[no-any-return]


@router.get("/privacy", response_class=Response)
async def privacy(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LegalPages["privacy"])  # type: ignore[no-any-return]


@router.get("/requisites", response_class=Response)
async def requisites(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, _LegalPages["requisites"])  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Shopfront
# ---------------------------------------------------------------------------


@router.get("/", response_class=Response)
async def shop_index(
    request: Request,
    nonce: str = Depends(csp_nonce),
) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "customer/index.html", {"csp_nonce": nonce}
    )


@router.get("/menu", response_class=Response)
async def shop_menu(
    request: Request,
    nonce: str = Depends(csp_nonce),
) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "customer/menu.html", {"csp_nonce": nonce}
    )


@router.get("/cart", response_class=Response)
async def shop_cart(
    request: Request,
    nonce: str = Depends(csp_nonce),
) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "customer/cart.html", {"csp_nonce": nonce}
    )


@router.get("/checkout", response_class=Response)
async def shop_checkout(
    request: Request,
    nonce: str = Depends(csp_nonce),
    schedule: ScheduleService = Depends(get_schedule_service),
) -> Response:
    window = await schedule.get_menu_window_context()
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "customer/checkout.html",
        {
            "csp_nonce": nonce,
            "is_open": window["is_open"],
            "show_delivery_notice": not window["is_open"],
            "preorder_info": window["preorder_info"],
            "morning_start": window["morning_start"],
            "morning_end": window["morning_end"],
            "evening_start": window["evening_start"],
            "evening_end": window["evening_end"],
        },
    )


@router.get("/closed", response_class=Response)
async def shop_closed(
    request: Request,
    nonce: str = Depends(csp_nonce),
    schedule: ScheduleService = Depends(get_schedule_service),
) -> Response:
    window = await schedule.get_menu_window_context()
    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "customer/closed.html",
        {
            "csp_nonce": nonce,
            "is_open": window["is_open"],
            "preorder_info": window["preorder_info"],
            "morning_start": window["morning_start"],
            "morning_end": window["morning_end"],
            "evening_start": window["evening_start"],
            "evening_end": window["evening_end"],
        },
    )


@router.get("/thanks/{order_id}", response_class=Response)
async def shop_thanks(
    request: Request,
    order_id: str,
    nonce: str = Depends(csp_nonce),
    order_service: OrderService = Depends(get_order_service),
) -> Response:
    import uuid

    try:
        parsed_id = uuid.UUID(order_id)
    except ValueError:
        parsed_id = None

    order = await order_service.get_order(str(parsed_id)) if parsed_id is not None else None
    delivery_fee = None
    if order is not None:
        # A non-empty delivery address means delivery (not pickup) — the flat
        # fee applies. Pickup orders have an empty delivery_address.
        delivery_fee = settings.DELIVERY_FEE if order.delivery_address else 0

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "customer/thanks.html",
        {
            "csp_nonce": nonce,
            "order": order,
            "delivery_fee": delivery_fee,
        },
    )
