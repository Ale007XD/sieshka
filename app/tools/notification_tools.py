"""
app/tools/notification_tools.py — nano-vm Tools for notifications.
M3+: registered with GovernedToolExecutor.

CONSTRAINTS:
  - Fire-and-forget — NOT inside PG transactions
  - Signature: async def fn(*, order_id: str, **kwargs) -> str

Import note: app.services.notification_service is imported LAZILY inside each
function, not at module level. app.services.__init__ imports OrderService,
which (as of sprint_max_staff_notify) imports notify_staff_new_kitchen_ticket
from this module — a top-level `from app.services.notification_service import
notification_service` here would trigger app.services.__init__ during this
module's own initialization, closing an import cycle:
    app.tools.notification_tools -> app.services.notification_service
    -> app.services.__init__ -> app.services.order_service
    -> app.tools.notification_tools (partially initialized -> ImportError)
The lazy import defers app.services.__init__ execution until call time, by
which point both modules have finished initializing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def notify_order_confirmed(*, order_id: str, **kwargs: object) -> str:
    from app.services.notification_service import notification_service

    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} подтверждён"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_confirmed: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_payment_received(*, order_id: str, **kwargs: object) -> str:
    from app.services.notification_service import notification_service

    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Оплата заказа {order_id} получена"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_payment_received: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_order_cooking(*, order_id: str, **kwargs: object) -> str:
    from app.services.notification_service import notification_service

    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} готовится"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_cooking: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_order_delivered(*, order_id: str, **kwargs: object) -> str:
    from app.services.notification_service import notification_service

    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} доставлен"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_delivered: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_order_failed(*, order_id: str, **kwargs: object) -> str:
    from app.services.notification_service import notification_service

    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} отменён"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_failed: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_staff_new_kitchen_ticket(
    *, order_id: str, ticket_id: str, **kwargs: object
) -> str:
    """sprint_max_staff_notify: broadcasts the new kitchen ticket to every
    active kitchen-role staff member over MAX, with a QUEUE button — the
    entry point into app.webhooks.max's role-gated dispatch. Wired as the
    terminal step of PROGRAM_START_COOKING (app/programs/order_programs.py),
    right after write_order_state_cooking, same fire-and-forget contract as
    the notify_order_* tools above (no PG session, never raises)."""
    from app.domains.kitchen.fsm import KitchenState
    from app.services.max_staff_notify import notify_kitchen_ticket_state

    try:
        await notify_kitchen_ticket_state(
            ticket_id=str(ticket_id), order_id=order_id, state=KitchenState.NEW
        )
    except Exception:
        # Defense-in-depth: notify_kitchen_ticket_state already swallows its
        # own exceptions, but this tool's contract (this is a governed
        # Program's terminal step) must never raise regardless — a raise
        # here would mark the whole Trace FAILED and, per order_service.py::
        # transition_order, skip session.commit() entirely, rolling back the
        # already-succeeded order->COOKING state write alongside it.
        logger.exception(
            "notify_staff_new_kitchen_ticket: notify failed order_id=%s ticket_id=%s",
            order_id,
            ticket_id,
        )
    logger.info(
        "notify_staff_new_kitchen_ticket: order_id=%s ticket_id=%s", order_id, ticket_id
    )
    return "NOTIFIED"
