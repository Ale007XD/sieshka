"""app/services/staff_dispatch.py — channel-agnostic role→event ACL and
dispatch into the governed KitchenService/OrderService FSMs.

Extracted from app/webhooks/max.py (sprint_zalo_miniapp_backend_api,
2026-08-13): the ACL dicts and _dispatch_kitchen/_dispatch_order functions
had zero MAX-specific coupling — they take (service, role, id, event_str)
only. Zalo's Mini App backend API needs the exact same role gating as the
MAX webhook (a kitchen-role staffer still can't CANCEL an order, regardless
of which channel they're pressing the button from). Duplicating the ACL a
second time (after it was already noted as deliberately duplicated between
webhooks/max.py and max_staff_notify.py, see AGENTS.md::technical_debt) was
the point at which "namerenno razdelno" stops being a reasonable tradeoff —
a third copy is a straightforward risk of the two ACLs drifting apart
silently. webhooks/max.py now imports from here instead of defining these
locally; behavior is unchanged (verified via existing test_max_webhook.py).

Role -> allowed event mapping (deliberately narrow; extend explicitly rather
than widening a role's blast radius by default):
    kitchen role -> any KitchenEvent, kitchen tickets only
    courier role -> ASSIGN_COURIER / PICKUP / DELIVER, orders only
    admin role   -> CANCEL, orders only
    staff role   -> full authority (2026-08-09, sieshka_staff_role) — every
                    KitchenEvent plus every OrderEvent courier/admin can
                    trigger, offered as one combined card/view
"""
from __future__ import annotations

from app.domains.kitchen.fsm import KitchenEvent
from app.domains.orders.models import OrderEvent
from app.domains.staff.models import StaffRole
from app.fsm.core.base import TransitionResult
from app.services.kitchen_service import KitchenService
from app.services.order_service import OrderService

_KITCHEN_ROLE_EVENTS: dict[StaffRole, frozenset[KitchenEvent]] = {
    StaffRole.kitchen: frozenset(KitchenEvent),
    StaffRole.staff: frozenset(KitchenEvent),
}
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

DENIED_MESSAGE = "Недопустимое действие для вашей роли"


async def dispatch_kitchen(
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


async def dispatch_order(
    orders: OrderService, role: StaffRole, order_id: str, event_str: str
) -> TransitionResult | None:
    try:
        event = OrderEvent(event_str)
    except ValueError:
        return None
    if event not in _ORDER_ROLE_EVENTS.get(role, frozenset()):
        return None
    return await orders.transition_order(order_id, event)
