from types import SimpleNamespace as NS

from app.services.order_dedup import REUSE_WINDOW_HOURS, items_signature


def _i(pid: str, price: int, qty: int) -> NS:
    return NS(product_id=pid, price_rub=price, qty=qty)


def test_signature_ignores_order():
    a = [_i("p1", 420, 1), _i("p2", 250, 2)]
    b = [_i("p2", 250, 2), _i("p1", 420, 1)]
    assert items_signature(a) == items_signature(b)


def test_signature_differs_on_qty_price_or_product():
    base = items_signature([_i("p1", 420, 1)])
    assert base != items_signature([_i("p1", 420, 2)])
    assert base != items_signature([_i("p1", 430, 1)])
    assert base != items_signature([_i("p2", 420, 1)])


def test_window_is_positive():
    assert REUSE_WINDOW_HOURS > 0
