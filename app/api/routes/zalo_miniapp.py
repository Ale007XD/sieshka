"""app/api/routes/zalo_miniapp.py — Zalo Mini App staff-action backend API.

sprint_zalo_miniapp_backend_api scope. Supersedes the originally-planned
"Zalo webhook adapter" mirroring app/webhooks/max.py's message_callback
dispatch: Zalo Mini App's actual Webhook URL (Open APIs section) only
carries two event types per official docs (mini.zalo.me/zmp-docs) — Mini
App review-status changes and user consent-revocation/data-deletion — NOT
button-press callbacks. There is no Zalo Mini App analog of MAX's inline-
keyboard message_callback. A Mini App is a webview: its frontend calls this
backend directly (fetch), authenticated per-request via
app.web.zalo_auth.get_current_zalo_staff — see that module's docstring for
why this differs from MAX's checkout-style initData verification.

Role gating (kitchen/courier/admin/staff) is identical to MAX's, by design
— see app/services/staff_dispatch.py, now shared by both channels rather
than duplicated a third time.

Response shape mirrors app/api/routes/kitchen.py's trigger_event and
app/api/routes/orders.py's transition endpoints (raw TransitionResult) for
consistency across every entry point into the same governed FSMs — the only
new thing here is the auth dependency and the role check before dispatch,
not a new response contract.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.domains.staff.models import Staff
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService
from app.services.order_service import OrderService
from app.services.staff_dispatch import DENIED_MESSAGE, dispatch_kitchen, dispatch_order
from app.web.zalo_auth import get_current_zalo_staff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/zalo", tags=["zalo-miniapp"])


def get_kitchen_service() -> KitchenService:
    return KitchenService()


def get_order_service() -> OrderService:
    return OrderService()


@router.post("/kitchen/{ticket_id}/transition")
async def zalo_kitchen_transition(
    ticket_id: str,
    event: str = Body(..., embed=True),
    staff: Staff = Depends(get_current_zalo_staff),
    kitchen: KitchenService = Depends(get_kitchen_service),
) -> TransitionResult:
    result = await dispatch_kitchen(kitchen, staff.role, ticket_id, event)
    if result is None:
        logger.warning(
            "Zalo Mini App: role=%s not permitted kitchen event=%s ticket=%s",
            staff.role,
            event,
            ticket_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DENIED_MESSAGE)
    return result


@router.post("/orders/{order_id}/transition")
async def zalo_order_transition(
    order_id: str,
    event: str = Body(..., embed=True),
    staff: Staff = Depends(get_current_zalo_staff),
    orders: OrderService = Depends(get_order_service),
) -> TransitionResult:
    result = await dispatch_order(orders, staff.role, order_id, event)
    if result is None:
        logger.warning(
            "Zalo Mini App: role=%s not permitted order event=%s order=%s",
            staff.role,
            event,
            order_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DENIED_MESSAGE)
    return result
