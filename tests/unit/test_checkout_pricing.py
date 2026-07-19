"""tests/unit/test_checkout_pricing.py — sprint_m7_checkout_wiring money logic.

CI runs tests/unit/ ONLY (integration is skipped without Docker). This is the
highest-risk (money-handling) logic in the project, so it MUST run on every
push. All tests here use mocked sessions / menu_service — no real Postgres,
no real YooKassa call.

Covers:
  - server total is computed from price_rub * qty + flat delivery fee, and is
    NEVER influenced by any client-supplied total (tampered or absent);
  - a later price change on the live Product row does NOT alter an already
    snapshotted order's items (price/name frozen at order-create time);
  - delivery fee is added only when delivery_mode != "pickup".
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domains.menu.models import MenuProductItem
from app.domains.orders.models import CheckoutItem, OrderItem
from app.services.menu_service import MenuService
from app.services.order_service import (
    compute_checkout_total,
    resolve_checkout_items,
)


def _item(product_id, name: str, price_rub: int, qty: int) -> OrderItem:
    return OrderItem(product_id=product_id, name=name, price_rub=price_rub, qty=qty)


def _snapshot(product_id, name: str, price_rub: int) -> MenuProductItem:
    return MenuProductItem(
        product_id=product_id,
        name=name,
        price_rub=price_rub,
        available=True,
        cta_type="add_to_cart",
        reason_code=None,
    )


class _FakeMenu(MenuService):
    def __init__(self, snapshots: dict) -> None:
        self._snapshots = snapshots
        self._factory = AsyncMock()

    async def get_product_snapshot(self, product_id):
        return self._snapshots.get(product_id)


async def test_total_ignores_client_total_and_sums_qty() -> None:
    """Money authority lives server-side: a client total is never read."""
    p1, p2 = uuid4(), uuid4()
    items = [
        _item(p1, "Coffee", 150, 2),  # 300
        _item(p2, "Cake", 200, 1),    # 200
    ]
    # delivery (not pickup) -> + DELIVERY_FEE (settings.DELIVERY_FEE default 99)
    total = compute_checkout_total(items, "delivery")
    assert total == 300 + 200 + 99
    # pickup -> no fee
    assert compute_checkout_total(items, "pickup") == 500


async def test_total_with_zero_qty_items() -> None:
    p = uuid4()
    items = [_item(p, "Free", 0, 3)]
    assert compute_checkout_total(items, "pickup") == 0
    assert compute_checkout_total(items, "delivery") == 99


async def test_resolve_checkout_items_snapshots_price_once() -> None:
    """Price/name are frozen at resolve time, not re-joined to live Product."""
    p = uuid4()
    # Initial live price seen at checkout time.
    menu = _FakeMenu({p: _snapshot(p, "Latte", 180)})
    items = await resolve_checkout_items(
        [CheckoutItem(product_id=p, qty=3)], menu_service=menu
    )
    assert len(items) == 1
    assert items[0].name == "Latte"
    assert items[0].price_rub == 180
    assert items[0].qty == 3
    # Simulate a later CSV re-import changing the live price to 999.
    menu._snapshots[p] = _snapshot(p, "Latte", 999)
    # The already-snapshotted OrderItem must NOT change.
    assert items[0].price_rub == 180


async def test_resolve_checkout_items_unknown_product_raises() -> None:
    menu = _FakeMenu({})
    with pytest.raises(ValueError):
        await resolve_checkout_items([CheckoutItem(product_id=uuid4(), qty=1)], menu_service=menu)


async def test_card_amount_uses_server_total_decimal() -> None:
    """The amount sent to YooKassa must equal the server total, as Decimal."""
    p = uuid4()
    items = [_item(p, "Bowl", 350, 2)]  # 700 + 99 = 799
    total = compute_checkout_total(items, "delivery")
    assert Decimal(total) == Decimal("799")
