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

Edit-in-place (2026-08-09): the original v1 tradeoff ("no message-editing,
each stage sends a NEW MAX message") is now implemented via
_send_or_edit()/app.services.max_message_refs — a (entity_kind, entity_id,
max_user_id) -> message_id tracking table. Every notify_* function now edits
the recipient's existing tracked message in place when one exists (including
the button-presser's own message: they're one of list_active_by_role()'s
recipients too, so the same broadcast loop naturally finds and edits it — no
separate app.webhooks.max/answer_callback special-casing needed), falling
back to sending a fresh message when there's no tracked ref yet or the
tracked one has gone stale (edit_message() returns False — e.g. the MAX-side
message was deleted or, for non-inline_keyboard messages, aged past MAX's
7-day edit window — see max_message_refs.py table docstring). Consequence: no
notify_* function early-returns on an empty keyboard anymore (that used to
mean "nothing to do" for kitchen/courier — now it means "edit the existing
message to show the terminal status, with no buttons", matching the
observer-role behavior notify_admin_order_state already had before this
change).

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
app.services.max_message_refs follows the same self-contained-session
pattern for the same reason.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text as sql_text

from app.db import async_session_factory
from app.domains.kitchen.fsm import KITCHEN_TRANSITIONS, KitchenEvent, KitchenState
from app.domains.orders.models import ORDER_TRANSITIONS, OrderEvent, OrderState
from app.domains.staff.models import StaffRole
from app.services.max_client import MaxClient, max_client
from app.services.max_message_refs import get_message_ref, save_message_ref
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
    StaffRole.staff: frozenset(
        {
            OrderEvent.ASSIGN_COURIER,
            OrderEvent.PICKUP,
            OrderEvent.DELIVER,
            OrderEvent.CANCEL,
        }
    ),
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
    # sprint_yookassa_manual_integration (2026-08-19): payment_method values
    # changed from "yookassa_card" to "yookassa_sbp"/"yookassa_sberbank" —
    # both are still non-cash YooKassa payments, same paid/pending display.
    if payment_method in ("yookassa_sbp", "yookassa_sberbank"):
        try:
            paid = (
                OrderState(order_state_raw) in _PAID_OR_LATER_STATES
                if order_state_raw
                else False
            )
        except ValueError:
            paid = False
        method_label = "СБП" if payment_method == "yookassa_sbp" else "SberPay"
        lines.append(f"💳 {method_label} — {'оплачено' if paid else 'ожидает оплаты'}")
    elif payment_method == "cash":
        lines.append("💵 Наличные — при получении")

    return ("\n" + "\n".join(lines)) if lines else ""


_GetRefFn = Callable[[str, str, int], Awaitable["str | None"]]
_SaveRefFn = Callable[[str, str, int, str], Awaitable[None]]


async def _send_or_edit(
    entity_kind: str,
    entity_id: str,
    max_user_id: int,
    text: str,
    keyboard: list[dict[str, Any]],
    client: MaxClient,
    *,
    get_ref: _GetRefFn = get_message_ref,
    save_ref: _SaveRefFn = save_message_ref,
) -> None:
    """Edit the recipient's existing tracked message if one exists; otherwise
    send a fresh message and start tracking it.

    get_ref/save_ref are injectable (default to the real
    app.services.max_message_refs functions) purely for unit testing without
    a DB — same DI pattern as this module's staff_service/client params.

    edit_message() returning False (stale/deleted/expired tracked message_id)
    falls through to sending a fresh message rather than silently dropping
    the update — see max_message_refs.py's max_message_refs table docstring.
    """
    existing_mid = await get_ref(entity_kind, entity_id, max_user_id)
    if existing_mid is not None:
        edited = await client.edit_message(existing_mid, text, attachments=keyboard)
        if edited:
            return
        # Stale ref — fall through and send fresh, overwriting it below.

    new_mid = await client.send_message(max_user_id, text, attachments=keyboard)
    if new_mid:
        await save_ref(entity_kind, entity_id, max_user_id, new_mid)


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
    triggered it (same contract as app.tools.notification_tools). Always
    sends/edits — even on a terminal state with no further kitchen-actionable
    event (HANDED_OFF), the existing tracked message is edited to show the
    final status with no buttons (2026-08-09, see module docstring).
    """
    keyboard = _kitchen_keyboard(ticket_id, state)

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
            await _send_or_edit("kitchen", ticket_id, member.max_user_id, text, keyboard, mc)
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

    Same fire-and-forget / always-send-or-edit contract as
    notify_kitchen_ticket_state (2026-08-09).
    """
    keyboard = _order_keyboard(order_id, state, StaffRole.courier)

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
            await _send_or_edit("order", order_id, member.max_user_id, text, keyboard, mc)
        except Exception:
            logger.exception(
                "notify_courier_order_state: send failed for max_user_id=%s",
                member.max_user_id,
            )


async def _fetch_order_state(order_id: str) -> OrderState | None:
    """Self-contained current-state lookup (own session — see module
    docstring). Used only by notify_admin_kitchen_ticket_state, which learns
    about a kitchen-side event but needs the order's CURRENT state to build
    the merged admin card (header text + CANCEL-button eligibility)."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                sql_text("SELECT state FROM orders WHERE id = :id"), {"id": order_id}
            )
            row = result.fetchone()
    except Exception:
        logger.exception("_fetch_order_state: query failed order_id=%s", order_id)
        return None
    if row is None:
        return None
    try:
        return OrderState(row._mapping["state"])
    except ValueError:
        return None


async def _fetch_kitchen_ticket_for_order(order_id: str) -> tuple[str, KitchenState] | None:
    """Self-contained lookup (own session) of the most recent kitchen ticket
    for an order, if one exists — returns (ticket_id, state). Returns None
    if no ticket exists yet (order hasn't reached COOKING) — a legitimate,
    common case, not an error.

    Used by notify_admin_order_state (only needs the state half — see
    _build_admin_card) and notify_staff_card (needs both: the id to build
    kitchen action buttons, the state to know which buttons apply)."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                sql_text(
                    "SELECT id, state FROM kitchen_tickets WHERE order_id = :id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"id": order_id},
            )
            row = result.fetchone()
    except Exception:
        logger.exception(
            "_fetch_kitchen_ticket_for_order: query failed order_id=%s", order_id
        )
        return None
    if row is None:
        return None
    try:
        return str(row._mapping["id"]), KitchenState(row._mapping["state"])
    except (KeyError, ValueError):
        return None


async def _build_admin_card(
    order_id: str,
    order_state: OrderState,
    kitchen_state: KitchenState | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Single admin card = order status + (if a kitchen ticket exists)
    kitchen-ticket status, in one message. Consolidated 2026-08-09 — the
    original design (notify_admin_order_state and
    notify_admin_kitchen_ticket_state each editing their OWN separate
    tracked message) produced two near-identical cards for the same order
    (one with CANCEL, one without), reported as looking like duplication.

    kitchen_state: pass the authoritative value when the caller already
    knows it (notify_admin_kitchen_ticket_state does — it's the event
    payload); pass None otherwise (notify_admin_order_state does) to have
    this function look it up via _fetch_kitchen_state_for_order. Both
    callers always pass their OWN axis's fresh value and let this function
    resolve the other axis — this is what keeps a kitchen-triggered edit
    from clobbering the order-status line and vice versa, since both writers
    target the same tracked message (entity_kind="order", entity_id=order_id).
    """
    keyboard = _order_keyboard(order_id, order_state, StaffRole.admin)
    details = await _fetch_order_details(order_id)
    resolved_kitchen_state = kitchen_state
    if resolved_kitchen_state is None:
        ticket = await _fetch_kitchen_ticket_for_order(order_id)
        resolved_kitchen_state = ticket[1] if ticket is not None else None
    kitchen_line = ""
    if resolved_kitchen_state is not None:
        kitchen_label = _KITCHEN_STATE_TEXT.get(
            resolved_kitchen_state, resolved_kitchen_state.value
        )
        kitchen_line = f"\n🍳 Кухня: {kitchen_label}"
    text = (
        f"🧾 Заказ {order_id[:8]}\n{_ORDER_STATE_TEXT.get(order_state, order_state.value)}"
        f"{kitchen_line}"
        f"{details}"
    )
    return text, keyboard


async def notify_admin_order_state(
    order_id: str,
    state: OrderState,
    *,
    staff_service: StaffService | None = None,
    client: MaxClient | None = None,
) -> None:
    """Broadcast to every active admin-role staff member.

    Admin is an OBSERVER role, not an actor: it always sends/edits the status
    line (kitchen/courier progress visibility, 2026-08-08 request), attaching
    the CANCEL button (per _ORDER_ROLE_EVENTS) only when the order is still
    in a cancellable state. Shares ONE tracked message per order with
    notify_admin_kitchen_ticket_state (see _build_admin_card, 2026-08-09) —
    do not reintroduce a second admin card for the same order_id.
    """
    text, keyboard = await _build_admin_card(order_id, state, kitchen_state=None)

    staff = staff_service or StaffService()
    mc = client or max_client

    try:
        recipients = await staff.list_active_by_role(StaffRole.admin)
    except Exception:
        logger.exception("notify_admin_order_state: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await _send_or_edit("order", order_id, member.max_user_id, text, keyboard, mc)
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
    """Update the SAME single admin card for this order (see
    _build_admin_card, 2026-08-09) with the kitchen ticket's current stage —
    NOT a separate card. ticket_id is accepted for call-site signature
    stability (webhooks.max/notification_tools both pass it) but no longer
    used to key the tracked message; the merged card is keyed by order_id
    alone, same as notify_admin_order_state.

    Admin has no kitchen-side action (kitchen-role ACL owns
    QUEUE/START_PREP/MARK_READY/HAND_OFF, see _ORDER_ROLE_EVENTS/
    webhooks.max role_gate) — the CANCEL button (if any) on this merged card
    still comes from the order's own state, exactly as in
    notify_admin_order_state.
    """
    order_state = await _fetch_order_state(order_id)
    if order_state is None:
        # Order row missing/DB hiccup — nothing sane to build against; same
        # defensive posture as _fetch_order_details returning "".
        return

    text, keyboard = await _build_admin_card(order_id, order_state, kitchen_state=state)

    staff = staff_service or StaffService()
    mc = client or max_client

    try:
        recipients = await staff.list_active_by_role(StaffRole.admin)
    except Exception:
        logger.exception("notify_admin_kitchen_ticket_state: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await _send_or_edit("order", order_id, member.max_user_id, text, keyboard, mc)
        except Exception:
            logger.exception(
                "notify_admin_kitchen_ticket_state: send failed for max_user_id=%s",
                member.max_user_id,
            )


def _combine_keyboards(*keyboards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge multiple single-row inline_keyboard attachments (as returned by
    _kitchen_keyboard/_order_keyboard) into ONE inline_keyboard attachment
    with multiple button rows — MAX supports several rows per keyboard, this
    just collects them under one attachment instead of stacking several
    inline_keyboard attachments. Empty inputs are skipped; returns [] if
    every input was empty (nothing actionable at all)."""
    rows: list[list[dict[str, Any]]] = []
    for kb in keyboards:
        for attachment in kb:
            rows.extend(attachment["payload"]["buttons"])
    if not rows:
        return []
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


async def _build_staff_card(
    order_id: str,
    order_state: OrderState,
    ticket_id: str | None,
    kitchen_state: KitchenState | None,
) -> tuple[str, list[dict[str, Any]]]:
    """Single card for the full-authority 'staff' role (2026-08-09): order
    status + kitchen status (if a ticket exists) + BOTH kitchen action
    buttons AND order action buttons (everything courier or admin could
    individually press) in one message, as two button rows.

    Mirrors _build_admin_card's "always resolve both axes fresh" contract
    (2026-08-09 admin-card-merge fix, same rationale here): two independent
    triggers (a kitchen event, an order event) edit the SAME tracked
    message, so each caller (notify_staff_card) always reconstructs the
    FULL card from both axes — never just the axis that changed — or the
    other axis's info/buttons would flicker or vanish on every edit that
    doesn't happen to know about it.
    """
    order_kb = _order_keyboard(order_id, order_state, StaffRole.staff)
    kitchen_kb: list[dict[str, Any]] = []
    if ticket_id is not None and kitchen_state is not None:
        kitchen_kb = _kitchen_keyboard(ticket_id, kitchen_state)
    keyboard = _combine_keyboards(kitchen_kb, order_kb)

    details = await _fetch_order_details(order_id)
    kitchen_line = ""
    if kitchen_state is not None:
        kitchen_label = _KITCHEN_STATE_TEXT.get(kitchen_state, kitchen_state.value)
        kitchen_line = f"\n🍳 Кухня: {kitchen_label}"
    text = (
        f"👤 Заказ {order_id[:8]}\n{_ORDER_STATE_TEXT.get(order_state, order_state.value)}"
        f"{kitchen_line}"
        f"{details}"
    )
    return text, keyboard


async def notify_staff_card(
    order_id: str,
    *,
    order_state: OrderState | None = None,
    ticket_id: str | None = None,
    kitchen_state: KitchenState | None = None,
    staff_service: StaffService | None = None,
    client: MaxClient | None = None,
) -> None:
    """Broadcast/edit the ONE combined card for the full-authority 'staff'
    role — order status + kitchen status + every button kitchen/courier/
    admin can individually press, per _ORDER_ROLE_EVENTS[StaffRole.staff]/
    _KITCHEN_ROLE_EVENTS[StaffRole.staff] (webhooks/max.py enforcement).

    Callers pass whichever axis they already know: an order-level trigger
    (checkout, or an order-kind webhook callback) passes order_state and
    leaves ticket_id/kitchen_state None; a kitchen-level trigger (ticket
    creation, or a kitchen-kind webhook callback) passes ticket_id+
    kitchen_state together and leaves order_state None. Either way this
    function resolves whatever's missing via DB lookup before building the
    card — see _build_staff_card docstring for why both axes are always
    resolved, not just the triggering one.
    """
    resolved_order_state = order_state
    if resolved_order_state is None:
        resolved_order_state = await _fetch_order_state(order_id)
    if resolved_order_state is None:
        # Order row missing/DB hiccup — nothing sane to build against, same
        # defensive posture as _fetch_order_details returning "".
        return

    resolved_ticket_id = ticket_id
    resolved_kitchen_state = kitchen_state
    if resolved_ticket_id is None or resolved_kitchen_state is None:
        ticket = await _fetch_kitchen_ticket_for_order(order_id)
        if ticket is not None:
            resolved_ticket_id, resolved_kitchen_state = ticket

    text, keyboard = await _build_staff_card(
        order_id, resolved_order_state, resolved_ticket_id, resolved_kitchen_state
    )

    staff = staff_service or StaffService()
    mc = client or max_client

    try:
        recipients = await staff.list_active_by_role(StaffRole.staff)
    except Exception:
        logger.exception("notify_staff_card: staff lookup failed")
        return

    for member in recipients:
        if member.max_user_id is None:
            continue
        try:
            await _send_or_edit("order", order_id, member.max_user_id, text, keyboard, mc)
        except Exception:
            logger.exception(
                "notify_staff_card: send failed for max_user_id=%s", member.max_user_id
            )
