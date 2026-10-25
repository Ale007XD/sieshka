"""app/services/customer_notify.py — customer-facing order-status push
(sprint_customer_notify, 2026-10-25).

Staff have had order-status push since sprint_max_staff_notify; the customer
who ordered through a Mini App had none — orders.client_max_uid /
client_telegram_uid (server-verified at checkout via X-Max-Init-Data /
X-Telegram-Init-Data HMAC) were written at checkout and then never read by
anything. This module is their consumer: one fire-and-forget call per
order-state change, delivering to whichever Mini App channel the order is
attributed to.

Contract (mirrors max_staff_notify): NEVER raises, never runs inside a PG
transaction (call after commit), silently no-ops when neither channel is
configured or the order has no Mini App attribution. Only customer-meaningful
states produce a message — DRAFT/CONFIRMED/PAYMENT_PENDING/PACKING/
COURIER_ASSIGNED/CLOSED stay silent to avoid noise, and PAID is represented
by the COOKING message that follows it seconds later on both payment paths.
PAYMENT_FAILED is an EVENT label, not an OrderState (the order lands back in
CONFIRMED) — fail_payment passes it explicitly so the customer learns the
payment fell through.

MAX Bot API caveat: a bot may only message a user who has its dialog open —
true in practice for anyone who launched the Mini App through the bot, but
a cold user_id that never opened the chat will simply not be delivered
(send_message logs and returns None; nothing raises).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.domains.orders.models import OrderState

logger = logging.getLogger(__name__)

# Keyed by str so both OrderState members and the PAYMENT_FAILED event label
# live in one map — see module docstring for why that label exists.
_STATE_TEXT: dict[str, str] = {
    OrderState.COOKING.value: "Заказ передан на кухню и готовится",
    OrderState.DELIVERING.value: "Курьер в пути с вашим заказом",
    OrderState.DELIVERED.value: "Заказ доставлен. Приятного аппетита!",
    OrderState.CANCELLED.value: "Заказ отменён",
    "PAYMENT_FAILED": "Оплата не прошла — можно оформить заказ заново",
}


async def notify_customer_order_state(order_id: str, new_state: object) -> None:
    """Fire-and-forget customer push for one order-state change; never raises.

    ``new_state`` is typed ``object`` because TransitionResult.new_state is
    (deliberately, in fsm/core/base) typed object — kitchen transitions carry
    KitchenState, order transitions carry OrderState. Normalized here:
    OrderState members by ``.value``, everything else via ``str()`` (the
    "PAYMENT_FAILED" event label arrives that way). Unknown values no-op.

    No-op without a message-worthy state, without any configured channel, or
    for an order without Mini App attribution.
    """
    raw = new_state.value if isinstance(new_state, OrderState) else str(new_state)
    state_text = _STATE_TEXT.get(raw)
    if state_text is None:
        return

    max_configured = bool(settings.MAX_BOT_TOKEN)
    tg_configured = bool(settings.TELEGRAM_BOT_TOKEN)
    if not (max_configured or tg_configured):
        logger.debug("customer_notify: no channel configured — skipping order %s", order_id)
        return

    try:
        order_uuid = UUID(order_id)
    except ValueError:
        logger.warning("customer_notify: order_id %r is not a UUID — skipping", order_id)
        return

    message = f"{state_text} (заказ {str(order_id)[:8]})"

    try:
        from app.db import async_session_factory

        async with async_session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT client_max_uid, client_telegram_uid FROM orders "
                        "WHERE id = :id"
                    ),
                    {"id": order_uuid},
                )
            ).one_or_none()
    except Exception:
        logger.exception("customer_notify: failed to load order %s", order_id)
        return
    if row is None:
        logger.warning("customer_notify: order %s not found — skipping", order_id)
        return

    max_uid = row._mapping["client_max_uid"]
    tg_uid = row._mapping["client_telegram_uid"]

    if max_configured and max_uid is not None:
        try:
            from app.services.max_client import max_client

            await max_client.send_message(int(max_uid), message)
        except Exception:
            logger.exception(
                "customer_notify: MAX send failed for order %s (uid=%s)", order_id, max_uid
            )

    if tg_configured and tg_uid is not None:
        try:
            from app.services.notification_service import notification_service

            await notification_service.send_telegram(str(tg_uid), message)
        except Exception:
            logger.exception(
                "customer_notify: Telegram send failed for order %s (uid=%s)",
                order_id, tg_uid,
            )
