"""app/services/max_staff_notify.py — role-targeted MAX staff order-status cards.

sprint_max_staff_notify: builds and sends the inline-keyboard notification
that pulls a kitchen/courier staff member into the governed transition loop
via app.webhooks.max's role-gated dispatch. This module never writes order or
kitchen state itself — it only broadcasts "here is what you're allowed to do
right now" to StaffService.list_active_by_role()'s audience for a given role.

Called from three places:
  1. app.tools.notification_tools.notify_staff_new_kitchen_ticket — governed
     Program TOOL step, fire-and-forget, wired as the terminal step of
     PROGRAM_START_COOKING (app/programs/order_programs.py).
  2. app.services.kitchen_service.KitchenService's existing HAND_OFF cascade
     (plain Python, not a Program) — notifies courier once an order reaches
     PACKING.
  3. app.webhooks.max, after a successful transition, to chain-notify the
     NEXT allowed step for the same role.

Design tradeoff (v1, stated explicitly rather than silently simplified): no
message-editing. Each stage sends a NEW MAX message rather than editing one
message in place (which is what SieshKa-Site's older bot does). Editing would
require persisting a MAX message_id per (ticket_or_order, recipient) pair —
multiple kitchen/courier staff can each receive their own message, so it's a
mapping table, not a single column. Deferred to a later sprint if the
multi-message UX proves annoying in practice; the FSM/ACL/dispatch
correctness does not depend on it.

Button payload contract (consumed by app.webhooks.max):
    {"kind": "kitchen", "id": "<ticket_uuid>", "event": "<KitchenEvent value>"}
    {"kind": "order",   "id": "<order_uuid>",  "event": "<OrderEvent value>"}
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.domains.kitchen.fsm import KITCHEN_TRANSITIONS, KitchenEvent, KitchenState
from app.domains.orders.models import ORDER_TRANSITIONS, OrderEvent, OrderState
from app.domains.staff.models import StaffRole
from app.services.max_client import MaxClient, max_client
from app.services.staff_service import StaffService

logger = logging.getLogger(__name__)

_KITCHEN_STATE_TEXT: dict[KitchenState, str] = {
    KitchenState.NEW: "🆕 Новый тикет",
    KitchenState.QUEUED: "📋 В очереди",
    KitchenState.PREPARING: "🍳 Готовится",
    KitchenState.READY: "✅ Готово к выдаче",
    KitchenState.HANDED_OFF: "📤 Передано",
}

_KITCHEN_EVENT_BUTTON: dict[KitchenEvent, tuple[str, str]] = {
    KitchenEvent.QUEUE: ("📋 В очередь", "default"),
    KitchenEvent.START_PREP: ("🍳 Начать готовить", "default"),
    KitchenEvent.MARK_READY: ("✅ Готово", "positive"),
    KitchenEvent.HAND_OFF: ("📤 Передать", "positive"),
}

_ORDER_STATE_TEXT: dict[OrderState, str] = {
    OrderState.PACKING: "📦 Упаковка",
    OrderState.COURIER_ASSIGNED: "🛵 Курьер назначен",
    OrderState.DELIVERING: "🚗 В пути",
    OrderState.DELIVERED: "✅ Доставлено",
    OrderState.CANCELLED: "❌ Отменён",
}

_ORDER_EVENT_BUTTON: dict[OrderEvent, tuple[str, str]] = {
    OrderEvent.ASSIGN_COURIER: ("🛵 Принять заказ", "positive"),
    OrderEvent.PICKUP: ("📦 Забрал", "default"),
    OrderEvent.DELIVER: ("✅ Доставил", "positive"),
    OrderEvent.CANCEL: ("❌ Отменить", "negative"),
}

# Role -> allowed events, offering-side mirror of app.webhooks.max's
# enforcement-side ACL. Kept as a separate constant deliberately (not
# imported from the webhook module): the webhook's map is what actually
# grants/denies a transition; this map only decides what button to OFFER.
# Offering a button the webhook would reject is a UX bug, not a security
# hole — the two are allowed to drift briefly, but should stay in sync.
_ORDER_ROLE_EVENTS: dict[StaffRole, frozenset[OrderEvent]] = {
    StaffRole.courier: frozenset(
        {OrderEvent.ASSIGN_COURIER, OrderEvent.PICKUP, OrderEvent.DELIVER}
    ),
    StaffRole.admin: frozenset({OrderEvent.CANCEL}),
}


def _kitchen_keyboard(ticket_id: str, state: KitchenState) -> list[dict[str, Any]]:
    allowed = KITCHEN_TRANSITIONS.get(state, {})
    row: list[dict[str, Any]] = []
    for event in allowed:
        label, intent = _KITCHEN_EVENT_BUTTON.get(event, (event.value, "default"))
        row.append(
            {
                "type": "callback",
                "text": label,
                "intent": intent,
                "payload": json.dumps(
                    {"kind": "kitchen", "id": ticket_id, "event": event.value},
                    ensure_ascii=False,
                ),
            }
        )
    if not row:
        return []
    return [{"type": "inline_keyboard", "payload": {"buttons": [row]}}]


def _order_keyboard(
    order_id: str, state: OrderState, role: StaffRole
) -> list[dict[str, Any]]:
    allowed = ORDER_TRANSITIONS.get(state, {})
    permitted = _ORDER_ROLE_EVENTS.get(role, frozenset())
    row: list[dict[str, Any]] = []
    for event in allowed:
        if event not in permitted:
            continue
        label, intent = _ORDER_EVENT_BUTTON.get(event, (event.value, "default"))
        row.append(
            {
                "type": "callback",
                "text": label,
                "intent": intent,
                "payload": json.dumps(
                    {"kind": "order", "id": order_id, "event": event.value},
                    ensure_ascii=False,
                ),
            }
        )
    if not row:
        return []
    return [{"type": "inline_keyboard", "payload": {"buttons": [row]}}]


async def notify_kitchen_ticket_state(
    ticket_id: str,
    order_id: str,
    state: KitchenState,
    *,
    staff_service: StaffService | None = None,
    client: MaxClient | None = None,
) -> None:
    """Broadcast to every active kitchen-role staff member.

    Fire-and-forget: every exception is caught and logged, never raised — a
    failed staff notification must never fail the governed transition that
    triggered it (same contract as app.tools.notification_tools). No-op if
    the state has no further kitchen-actionable event (HANDED_OFF).
    """
    keyboard = _kitchen_keyboard(ticket_id, state)
    if not keyboard:
        return

    staff = staff_service or StaffService()
    mc = client or max_client
    text = f"🍳 Заказ {order_id[:8]}\n{_KITCHEN_STATE_TEXT.get(state, state.value)}"

    try:
        recipients = await staff.list_active_by_role(StaffRole.kitchen)
    except Exception:
        logger.exception("notify_kitchen_ticket_state: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await mc.send_message(member.max_user_id, text, attachments=keyboard)
        except Exception:
            logger.exception(
                "notify_kitchen_ticket_state: send failed for max_user_id=%s",
                member.max_user_id,
            )


async def notify_courier_order_state(
    order_id: str,
    state: OrderState,
    *,
    staff_service: StaffService | None = None,
    client: MaxClient | None = None,
) -> None:
    """Broadcast to every active courier-role staff member.

    Same fire-and-forget / no-op-if-nothing-actionable contract as
    notify_kitchen_ticket_state.
    """
    keyboard = _order_keyboard(order_id, state, StaffRole.courier)
    if not keyboard:
        return

    staff = staff_service or StaffService()
    mc = client or max_client
    text = f"🚗 Заказ {order_id[:8]}\n{_ORDER_STATE_TEXT.get(state, state.value)}"

    try:
        recipients = await staff.list_active_by_role(StaffRole.courier)
    except Exception:
        logger.exception("notify_courier_order_state: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await mc.send_message(member.max_user_id, text, attachments=keyboard)
        except Exception:
            logger.exception(
                "notify_courier_order_state: send failed for max_user_id=%s",
                member.max_user_id,
            )


async def notify_admin_order_state(
    order_id: str,
    state: OrderState,
    *,
    staff_service: StaffService | None = None,
    client: MaxClient | None = None,
) -> None:
    """Broadcast to every active admin-role staff member — CANCEL only,
    per _ORDER_ROLE_EVENTS. No-op (no message sent, no staff lookup even
    attempted) once the order leaves a cancellable state (COOKING onward) —
    same keyboard-empty short-circuit as notify_courier_order_state, so
    callers can invoke this unconditionally after any order transition
    without checking ORDER_TRANSITIONS themselves.

    Same fire-and-forget / no-op-if-nothing-actionable contract as
    notify_kitchen_ticket_state / notify_courier_order_state.
    """
    keyboard = _order_keyboard(order_id, state, StaffRole.admin)
    if not keyboard:
        return

    staff = staff_service or StaffService()
    mc = client or max_client
    text = f"🧾 Заказ {order_id[:8]}\n{_ORDER_STATE_TEXT.get(state, state.value)}"

    try:
        recipients = await staff.list_active_by_role(StaffRole.admin)
    except Exception:
        logger.exception("notify_admin_order_state: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await mc.send_message(member.max_user_id, text, attachments=keyboard)
        except Exception:
            logger.exception(
                "notify_admin_order_state: send failed for max_user_id=%s",
                member.max_user_id,
            )
