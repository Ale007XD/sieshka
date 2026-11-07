import json

from app.services.payment_display import (
    PAID,
    UNPAID,
    can_mark_cash_paid,
    payment_keyboard,
    payment_line,
)


def test_line_cash_unpaid():
    assert payment_line(1279, "cash", UNPAID) == "💰 1 279 ₽ · Наличные · 🔴 НЕ ОПЛАЧЕНО"


def test_line_yookassa_paid():
    assert payment_line(1279, "yookassa_sbp", PAID) == "💰 1 279 ₽ · СБП · 🟢 ОПЛАЧЕНО"


def test_keyboard_only_for_cash():
    assert payment_keyboard("o1", "yookassa_sbp", PAID) == []
    assert payment_keyboard("o1", None, UNPAID) == []


def _button(kb):
    return kb[0]["payload"]["buttons"][0][0]


def test_cash_unpaid_is_red_and_marks_paid():
    b = _button(payment_keyboard("o1", "cash", UNPAID))
    assert b["intent"] == "negative" and "НЕ ОПЛАЧЕНО" in b["text"]
    assert json.loads(b["payload"]) == {"kind": "payment", "id": "o1", "event": "MARK_PAID"}


def test_cash_paid_is_green_noop():
    b = _button(payment_keyboard("o1", "cash", PAID))
    assert b["intent"] == "positive"
    assert json.loads(b["payload"])["event"] == "NOOP"


def test_roles():
    assert can_mark_cash_paid("admin") and can_mark_cash_paid("staff")
    assert can_mark_cash_paid("courier")
    assert not can_mark_cash_paid("kitchen")
