"""
app/tools/notification_tools.py — nano-vm Tools for notifications.
M3+: registered with GovernedToolExecutor.

CONSTRAINTS:
  - Fire-and-forget — NOT inside PG transactions
  - Signature: async def fn(*, order_id: str, **kwargs) -> str
"""
from __future__ import annotations

import logging

from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)


async def notify_order_confirmed(*, order_id: str, **kwargs: object) -> str:
    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} подтверждён"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_confirmed: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_payment_received(*, order_id: str, **kwargs: object) -> str:
    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Оплата заказа {order_id} получена"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_payment_received: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_order_cooking(*, order_id: str, **kwargs: object) -> str:
    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} готовится"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_cooking: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_order_delivered(*, order_id: str, **kwargs: object) -> str:
    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} доставлен"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_delivered: order_id=%s", order_id)
    return "NOTIFIED"


async def notify_order_failed(*, order_id: str, **kwargs: object) -> str:
    chat_id = str(kwargs.get("chat_id", ""))
    message = f"Заказ {order_id} отменён"
    await notification_service.send_telegram(chat_id, message)
    logger.info("notify_order_failed: order_id=%s", order_id)
    return "NOTIFIED"
