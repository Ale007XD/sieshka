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

Order detail enrichment (2026-08-08): kitchen/courier/admin cards originally
carried only the order's short id + state label — not enough for kitchen to
actually prep the right items or for courier/admin to find the address.
_fetch_order_details() below is a self-contained, read-only lookup (own
session via app.db.async_session_factory, raw SQL) rather than importing
app.services.order_service — mirrors the reason app.tools.notification_tools
and app.services.kitchen_service both keep their own imports of THIS module
lazy (see their source): importing order_service here would add a new
module-level edge into the app.services.__init__ eager-import chain that
does not currently exist, for a read-only convenience that doesn't need it.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sql_text

from app.db import async_session_factory
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
    OrderState.CONFIRMED: "🆕 Новый заказ",
    OrderState.PAYMENT_PENDING: "🆕 Новый заказ (ожидает оплаты)",
    OrderState.PAID: "🆕 Новый заказ (оплачен)",
    OrderState.COOKING: "🍳 Готовится",
    OrderState.PACKING: "📦 Упаковка",
    OrderState.COURIER_ASSIGNED: "🛵 Курьер назначен",
    OrderState.DELIVERING: "🚗 В пути",
    OrderState.DELIVERED: "✅ Доставлено",
    OrderState.CLOSED: "🏁 Завершён",
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

# States reached only after a successful YooKassa payment (PAID onward, minus
# the terminal failure/cancel states). Used solely to phrase the payment-status
# line in _fetch_order_details — "оплачено" vs "ожидает оплаты" — never for
# any transition/authorization decision, which stays entirely in
# ORDER_TRANSITIONS/OrderService.
_PAID_OR_LATER_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.PAID,
        OrderState.COOKING,
        OrderState.PACKING,
        OrderState.COURIER_ASSIGNED,
        OrderState.DELIVERING,
        OrderState.DELIVERED,
        OrderState.CLOSED,
    }
)


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


async def _fetch_order_details(order_id: str) -> str:
    """Read-only composition/address/phone block for notification text.

    Self-contained session (own async_session_factory call) — see module
    docstring for why this does not import app.services.order_service.
    Returns "" (not raised) on any failure or missing row: this is called
    from inside notify_* functions that must never let a detail-formatting
    problem block sending the base state notification.
    """
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                sql_text(
                    "SELECT o.items, o.delivery_address, o.comment, "
                    "o.payment_method, o.state AS order_state, "
                    "c.phone AS customer_phone "
                    "FROM orders o LEFT JOIN customers c ON c.id = o.customer_id "
                    "WHERE o.id = :id"
                ),
                {"id": order_id},
            )
            row = result.fetchone()
    except Exception:
        logger.exception("_fetch_order_details: query failed for order_id=%s", order_id)
        return ""

    if row is None:
        return ""

    items_val = row._mapping["items"]
    try:
        items = items_val if isinstance(items_val, list) else json.loads(items_val)
    except Exception:
        items = []

    lines: list[str] = []
    composition = ", ".join(
        f"{item.get('name', '?')} x{item.get('qty', '?')}"
        for item in items
        if isinstance(item, dict)
    )
    if composition:
        lines.append(f"🧺 {composition}")

    address = row._mapping.get("delivery_address")
    if address:
        lines.append(f"📍 {address}")

    phone = row._mapping.get("customer_phone")
    if phone:
        lines.append(f"📞 {phone}")

    comment = row._mapping.get("comment")
    if comment:
        lines.append(f"💬 {comment}")

    payment_method = row._mapping.get("payment_method")
    order_state_raw = row._mapping.get("order_state")
    if payment_method == "yookassa_card":
        try:
            paid = (
                OrderState(order_state_raw) in _PAID_OR_LATER_STATES
                if order_state_raw
                else False
            )
        except ValueError:
            paid = False
        lines.append(f"💳 ЮKassa — {'оплачено' if paid else 'ожидает оплаты'}")
    elif payment_method == "cash":
        lines.append("💵 Наличные — при получении")

    return ("\n" + "\n".join(lines)) if lines else ""


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
    details = await _fetch_order_details(order_id)
    text = (
        f"🍳 Заказ {order_id[:8]}\n{_KITCHEN_STATE_TEXT.get(state, state.value)}"
        f"{details}"
    )

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
    details = await _fetch_order_details(order_id)
    text = (
        f"🚗 Заказ {order_id[:8]}\n{_ORDER_STATE_TEXT.get(state, state.value)}"
        f"{details}"
    )

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
    """Broadcast to every active admin-role staff member.

    Unlike notify_kitchen_ticket_state/notify_courier_order_state, admin is
    an OBSERVER role, not an actor: it always sends the status line (kitchen/
    courier progress visibility, 2026-08-08 request), attaching the CANCEL
    button (per _ORDER_ROLE_EVENTS) only when the order is still in a
    cancellable state. Deliberately does NOT no-op when the keyboard is
    empty — that early-return is correct for kitchen/courier (nothing to do
    without a button) but wrong for admin (still wants to see "courier
    delivered", "order closed", etc. with no button attached).
    """
    keyboard = _order_keyboard(order_id, state, StaffRole.admin)

    staff = staff_service or StaffService()
    mc = client or max_client
    details = await _fetch_order_details(order_id)
    text = (
        f"🧾 Заказ {order_id[:8]}\n{_ORDER_STATE_TEXT.get(state, state.value)}"
        f"{details}"
    )

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


async def notify_admin_kitchen_ticket_state(
    ticket_id: str,
    order_id: str,
    state: KitchenState,
    *,
    staff_service: StaffService | None = None,
    client: MaxClient | None = None,
) -> None:
    """Broadcast kitchen-ticket progress to every active admin-role staff
    member — informational only, no keyboard. Admin has no kitchen-side
    action (kitchen-role ACL owns QUEUE/START_PREP/MARK_READY/HAND_OFF, see
    _ORDER_ROLE_EVENTS/webhooks.max role_gate) — this exists purely so admin
    sees the same NEW→QUEUED→PREPARING→READY→HANDED_OFF progression kitchen
    staff act on, per 2026-08-08 request ("статус менялся при изменении
    кухней"). Always sends (no _kitchen_keyboard-emptiness gate — that check
    exists to decide whether kitchen has a next action, irrelevant here).
    """
    staff = staff_service or StaffService()
    mc = client or max_client
    details = await _fetch_order_details(order_id)
    text = (
        f"🍳 Заказ {order_id[:8]} — кухня\n{_KITCHEN_STATE_TEXT.get(state, state.value)}"
        f"{details}"
    )

    try:
        recipients = await staff.list_active_by_role(StaffRole.admin)
    except Exception:
        logger.exception("notify_admin_kitchen_ticket_state: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await mc.send_message(member.max_user_id, text, attachments=[])
        except Exception:
            logger.exception(
                "notify_admin_kitchen_ticket_state: send failed for max_user_id=%s",
                member.max_user_id,
            )
