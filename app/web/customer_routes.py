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
from app.domains.orders.models import ORDER_STATE_LABELS_RU
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
    goods_total = None
    state_label = None
    if order is not None:
        goods_total = sum(item.price_rub * item.qty for item in order.items)
        if order.total_rub is not None:
            # total_rub already nets out discount_rub (compute_checkout_total
            # subtracts it before adding the delivery fee) — delivery_fee here
            # is a pure remainder, not "total - goods", so it must add the
            # discount back before subtracting goods, or a discounted order
            # would show an artificially-low (or negative) delivery fee.
            delivery_fee = order.total_rub - goods_total + (order.discount_rub or 0)
        else:
            # Pre-migration orders (no total_rub recorded) — fall back to the
            # old flat-fee heuristic rather than showing nothing.
            delivery_fee = settings.DELIVERY_FEE if order.delivery_address else 0
        state_label = ORDER_STATE_LABELS_RU.get(order.state, order.state.value)

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "customer/thanks.html",
        {
            "csp_nonce": nonce,
            "order": order,
            "delivery_fee": delivery_fee,
            "goods_total": goods_total,
            "state_label": state_label,
        },
    )