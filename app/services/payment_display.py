"""app/services/payment_display.py — how payment is shown on staff cards (pure).

Rules (owner, 2026-11-07):
  * every card shows the order total and the payment method;
  * YooKassa orders show 🟢 ОПЛАЧЕНО once the verified webhook marked them paid;
  * cash orders carry a RED button «НЕ ОПЛАЧЕНО»; admin / staff(hostess) /
    courier can press it -> 🟢 ОПЛАЧЕНО; every role sees the status line.
"""
from __future__ import annotations

import json
from typing import Any

PAID = "PAID"
UNPAID = "UNPAID"

# Callback payload (same {kind,id,event} contract as kitchen/order buttons).
KIND_PAYMENT = "payment"
EVENT_MARK_PAID = "MARK_PAID"
EVENT_NOOP = "NOOP"  # the green «ОПЛАЧЕНО» button: acknowledged, does nothing

# staff.role values allowed to flip a cash order to paid.
CASH_MARK_ROLES: frozenset[str] = frozenset({"admin", "staff", "courier"})

_METHOD_LABELS = {
    "cash": "Наличные",
    "yookassa_sbp": "СБП",
    "yookassa_sberbank": "SberPay",
}


def payment_method_label(method: str | None) -> str:
    if not method:
        return "—"
    return _METHOD_LABELS.get(method, method)


def format_rub(total_rub: int | None) -> str:
    if total_rub is None:
        return "—"
    return f"{total_rub:,}".replace(",", " ") + " ₽"


def payment_line(total_rub: int | None, method: str | None, status: str) -> str:
    """Text line for every card: «💰 1 279 ₽ · Наличные · 🔴 НЕ ОПЛАЧЕНО»."""
    mark = "🟢 ОПЛАЧЕНО" if status == PAID else "🔴 НЕ ОПЛАЧЕНО"
    return f"💰 {format_rub(total_rub)} · {payment_method_label(method)} · {mark}"


def payment_keyboard(order_id: str, method: str | None, status: str) -> list[dict[str, Any]]:
    """MAX inline_keyboard attachment with the cash button; [] for non-cash."""
    if method != "cash":
        return []
    paid = status == PAID
    button = {
        "type": "callback",
        "text": "🟢 ОПЛАЧЕНО" if paid else "🔴 НЕ ОПЛАЧЕНО",
        "intent": "positive" if paid else "negative",
        "payload": json.dumps(
            {
                "kind": KIND_PAYMENT,
                "id": order_id,
                "event": EVENT_NOOP if paid else EVENT_MARK_PAID,
            },
            ensure_ascii=False,
        ),
    }
    return [{"type": "inline_keyboard", "payload": {"buttons": [[button]]}}]


def can_mark_cash_paid(role: str) -> bool:
    return role in CASH_MARK_ROLES
