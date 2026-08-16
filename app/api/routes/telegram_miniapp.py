"""app/api/routes/telegram_miniapp.py — Telegram Mini App staff-action
backend API (sprint_telegram_miniapp_backend_api).

Same shape as app/api/routes/zalo_miniapp.py — a Mini App is a webview,
its frontend calls this backend directly (fetch), authenticated per-request
via app.web.telegram_auth.get_current_telegram_staff. Unlike Zalo, Telegram
bots also support inline-keyboard callback_query dispatch (see MAX's
webhooks/max.py pattern) — that route stays available for
sprint_telegram_bot_entrypoint (chat-based staff actions), this one is
specifically for the Mini App webview surface. Both would go through the
same staff_dispatch.py ACL either way, so the two are complementary
entry points into the same governed FSMs, not competing designs.

Role gating identical to MAX/Zalo — app/services/staff_dispatch.py, shared
across all three channels rather than duplicated a third/fourth time.

Response shape mirrors zalo_miniapp.py/kitchen.py/orders.py (raw
TransitionResult) — only the auth dependency changes per channel.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.domains.staff.models import Staff
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService
from app.services.order_service import OrderService
from app.services.staff_dispatch import DENIED_MESSAGE, dispatch_kitchen, dispatch_order
from app.web.telegram_auth import get_current_telegram_staff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram-miniapp"])


def get_kitchen_service() -> KitchenService:
    return KitchenService()


def get_order_service() -> OrderService:
    return OrderService()


@router.post("/kitchen/{ticket_id}/transition")
async def telegram_kitchen_transition(
    ticket_id: str,
    event: str = Body(..., embed=True),
    staff: Staff = Depends(get_current_telegram_staff),
    kitchen: KitchenService = Depends(get_kitchen_service),
) -> TransitionResult:
    result = await dispatch_kitchen(kitchen, staff.role, ticket_id, event)
    if result is None:
        logger.warning(
            "Telegram Mini App: role=%s not permitted kitchen event=%s ticket=%s",
            staff.role,
            event,
            ticket_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DENIED_MESSAGE)
    return result


@router.post("/orders/{order_id}/transition")
async def telegram_order_transition(
    order_id: str,
    event: str = Body(..., embed=True),
    staff: Staff = Depends(get_current_telegram_staff),
    orders: OrderService = Depends(get_order_service),
) -> TransitionResult:
    result = await dispatch_order(orders, staff.role, order_id, event)
    if result is None:
        logger.warning(
            "Telegram Mini App: role=%s not permitted order event=%s order=%s",
            staff.role,
            event,
            order_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=DENIED_MESSAGE)
    return result
