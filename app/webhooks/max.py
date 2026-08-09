"""app/webhooks/max.py — MAX Bot API webhook: role-gated staff callback dispatch.

sprint_max_webhook_adapter scope: message_callback handling only — routes a
button press through StaffService (role_gate) into the ALREADY-governed
KitchenService.transition_ticket() / OrderService.transition_order() — the
exact same services app/api/routes/kitchen.py and orders.py call over HTTP;
this adapter calls them in-process via the same constructor shape. No new
transition logic is introduced here, only a new entry point into the
existing FSMs.

bot_started/message_created (the storefront /start reply, initData-linked
webview launch) are intentionally stubbed — that belongs to
sprint_max_storefront.

Callback payload contract (produced by the button-builder in the upcoming
sprint_max_staff_notify, consumed here):
    {"kind": "kitchen", "id": "<ticket_uuid>", "event": "<KitchenEvent value>"}
    {"kind": "order",   "id": "<order_uuid>",  "event": "<OrderEvent value>"}

Role -> allowed event mapping (deliberately narrow; extend explicitly rather
than widening a role's blast radius by default):
    kitchen role -> any KitchenEvent, kitchen tickets only
    courier role -> ASSIGN_COURIER / PICKUP / DELIVER, orders only
    admin role   -> CANCEL, orders only — sprint_staff_table scoped admin to
                    monitoring/stats-first; wider admin override is a future
                    sprint, not a silent default here

Ported from SieshKa-Site/app/main.py::max_callback: header secret check,
callback field extraction (callback_id / user.user_id / payload|data), JSON
payload parsing. What does NOT port: the ACL/dispatch below that extraction
— the old handler used a flat MAX_ALLOWED_USER_IDS allowlist against a single
hand-rolled OrderStatus graph (no roles). Here dispatch is by staff.role via
StaffService against the two real governed FSMs.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.domains.kitchen.fsm import KitchenEvent, KitchenState
from app.domains.orders.models import OrderEvent, OrderState
from app.domains.staff.models import StaffRole
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService
from app.services.max_client import MaxClient, max_client
from app.services.max_staff_notify import (
    notify_admin_kitchen_ticket_state,
    notify_admin_order_state,
    notify_courier_order_state,
    notify_kitchen_ticket_state,
)
from app.services.order_service import OrderService
from app.services.staff_service import StaffService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_KITCHEN_ROLE_EVENTS: dict[StaffRole, frozenset[KitchenEvent]] = {
    StaffRole.kitchen: frozenset(KitchenEvent),
}
_ORDER_ROLE_EVENTS: dict[StaffRole, frozenset[OrderEvent]] = {
    StaffRole.courier: frozenset(
        {OrderEvent.ASSIGN_COURIER, OrderEvent.PICKUP, OrderEvent.DELIVER}
    ),
    StaffRole.admin: frozenset({OrderEvent.CANCEL}),
}

_DENIED = "Недопустимое действие для вашей роли"


def get_staff_service() -> StaffService:
    return StaffService()


def get_kitchen_service() -> KitchenService:
    return KitchenService()


def get_order_service() -> OrderService:
    return OrderService()


def get_max_client() -> MaxClient:
    return max_client


async def _dispatch_kitchen(
    kitchen: KitchenService, role: StaffRole, ticket_id: str, event_str: str
) -> TransitionResult | None:
    """None means "not permitted" (unknown event or role lacks it) —
    distinct from a governed rejection (TransitionResult(success=False))."""
    try:
        event = KitchenEvent(event_str)
    except ValueError:
        return None
    if event not in _KITCHEN_ROLE_EVENTS.get(role, frozenset()):
        return None
    return await kitchen.transition_ticket(ticket_id, event)


async def _dispatch_order(
    orders: OrderService, role: StaffRole, order_id: str, event_str: str
) -> TransitionResult | None:
    try:
        event = OrderEvent(event_str)
    except ValueError:
        return None
    if event not in _ORDER_ROLE_EVENTS.get(role, frozenset()):
        return None
    return await orders.transition_order(order_id, event)


def _result_notification(result: TransitionResult, fallback_label: str) -> str:
    if result.success:
        new_state = getattr(result.new_state, "value", result.new_state)
        text = f"✅ {new_state or fallback_label}"
    else:
        text = f"❌ {result.reason or 'Переход отклонён'}"
    return text[:64]  # MAX /answers notification limit


@router.post("/max")
async def max_webhook(
    request: Request,
    staff: StaffService = Depends(get_staff_service),
    kitchen: KitchenService = Depends(get_kitchen_service),
    orders: OrderService = Depends(get_order_service),
    client: MaxClient = Depends(get_max_client),
) -> JSONResponse:
    secret = request.headers.get("X-Max-Bot-Api-Secret")
    if settings.MAX_WEBHOOK_SECRET and secret != settings.MAX_WEBHOOK_SECRET:
        logger.warning("MAX webhook: invalid webhook secret")
        return JSONResponse({"ok": False}, status_code=403)

    try:
        update: dict[str, object] = await request.json()
    except Exception:
        logger.warning("MAX webhook: invalid JSON body")
        return JSONResponse({"ok": True})  # never fail MAX's delivery retry loop

    update_type = update.get("update_type")

    if update_type in ("bot_started", "message_created"):
        logger.info(
            "MAX webhook: %s acknowledged, not handled (sprint_max_storefront scope)",
            update_type,
        )
        return JSONResponse({"ok": True})

    if update_type != "message_callback":
        logger.debug("MAX webhook: ignored update_type=%r", update_type)
        return JSONResponse({"ok": True, "ignored": True})

    callback = update.get("callback")
    if not isinstance(callback, dict):
        return JSONResponse({"ok": True})

    callback_id = callback.get("callback_id")
    sender = callback.get("user") or {}
    try:
        sender_id: int | None = int(sender.get("user_id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        sender_id = None

    if sender_id is None:
        logger.warning("MAX webhook: callback without a resolvable sender_id")
        return JSONResponse({"ok": True})

    staff_member = await staff.find_by_max_user_id(sender_id)
    if staff_member is None:
        logger.warning("MAX webhook: unknown/inactive staff max_user_id=%s", sender_id)
        if callback_id:
            await client.answer_callback(callback_id, notification="Доступ запрещён")
        return JSONResponse({"ok": True})

    raw_payload = callback.get("payload") or callback.get("data")
    try:
        payload = json.loads(raw_payload or "")
    except (TypeError, ValueError):
        logger.warning("MAX webhook: invalid callback payload=%r", raw_payload)
        if callback_id:
            await client.answer_callback(
                callback_id, notification="Некорректные данные кнопки"
            )
        return JSONResponse({"ok": True})

    kind = payload.get("kind")
    entity_id = payload.get("id")
    event_str = payload.get("event")
    if kind not in ("kitchen", "order") or not entity_id or not event_str:
        logger.warning("MAX webhook: incomplete payload=%r", payload)
        if callback_id:
            await client.answer_callback(callback_id, notification="Недостаточно данных")
        return JSONResponse({"ok": True})

    if kind == "kitchen":
        result = await _dispatch_kitchen(
            kitchen, staff_member.role, str(entity_id), str(event_str)
        )
    else:
        result = await _dispatch_order(
            orders, staff_member.role, str(entity_id), str(event_str)
        )

    if result is None:
        logger.warning(
            "MAX webhook: role=%s not permitted kind=%s event=%s",
            staff_member.role,
            kind,
            event_str,
        )
        if callback_id:
            await client.answer_callback(callback_id, notification=_DENIED)
        return JSONResponse({"ok": True})

    if callback_id:
        await client.answer_callback(
            callback_id, notification=_result_notification(result, str(event_str))
        )

    if result.success and result.new_state is not None:
        # sprint_max_staff_notify chain-notify: v1 has no message-editing (see
        # app/services/max_staff_notify.py docstring for the tradeoff), so the
        # NEXT allowed action is sent as a new message rather than updating
        # this one. This is itself fire-and-forget — never lets a notify
        # failure surface as a webhook error, since the governed transition
        # already succeeded and answered above.
        try:
            if kind == "kitchen":
                order_id = await kitchen.get_order_id(str(entity_id))
                if order_id is not None and isinstance(result.new_state, KitchenState):
                    await notify_kitchen_ticket_state(
                        str(entity_id),
                        order_id,
                        result.new_state,
                        staff_service=staff,
                        client=client,
                    )
                    # 2026-08-08: admin observes kitchen-ticket progress too
                    # (NEW→QUEUED→PREPARING→READY→HANDED_OFF) — admin has no
                    # action here (informational only, see function docstring).
                    await notify_admin_kitchen_ticket_state(
                        str(entity_id),
                        order_id,
                        result.new_state,
                        staff_service=staff,
                        client=client,
                    )
            elif staff_member.role == StaffRole.courier and isinstance(
                result.new_state, OrderState
            ):
                await notify_courier_order_state(
                    str(entity_id), result.new_state, staff_service=staff, client=client
                )
            if kind == "order" and isinstance(result.new_state, OrderState):
                await notify_admin_order_state(
                    str(entity_id), result.new_state, staff_service=staff, client=client
                )
        except Exception:
            logger.exception(
                "MAX webhook: chain-notify failed for kind=%s id=%s", kind, entity_id
            )

    return JSONResponse({"ok": True})
