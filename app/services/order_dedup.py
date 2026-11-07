"""app/services/order_dedup.py — checkout retry de-duplication helpers (pure).

2026-11-07 incident: a customer whose YooKassa payment was canceled retried
checkout and every retry created a NEW order (six identical orders in two
hours), each left in CONFIRMED. OrderService.find_reusable_order uses these
helpers to recognise "same cart again" and continue the existing order.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domains.orders.models import OrderItem

# A CONFIRMED order older than this is not reused (customer's intent is stale,
# prices may have changed): a fresh order is created instead.
REUSE_WINDOW_HOURS = 3


def items_signature(items: Iterable[OrderItem]) -> tuple[tuple[str, int, int], ...]:
    """Order-independent identity of a cart: sorted (product_id, price_rub, qty)."""
    return tuple(
        sorted((str(i.product_id), int(i.price_rub), int(i.qty)) for i in items)
    )
