"""tests/unit/test_thanks_page_cart_clear_contract.py — thanks.html cart-clear gate.

sprint_fix_online_payment_funnel (2026-08-19): cart clearing for the online
payment path moved from cart.js (payment-link-creation time) to here
(customer/thanks.html), gated on the order having actually reached a real
paid/in-progress state. See test_cart_js_checkout_flow.py for the cart.js
side of this fix and the full rationale.

DRAFT/CONFIRMED/PAYMENT_PENDING/CANCELLED must NOT trigger a clear — those
states mean the payment either hasn't happened yet or never will. Everything
past PAYMENT_PENDING (PAID and onward) means the order genuinely progressed.

No Docker / DB required: it only scans the template source on disk (a
Jinja2 static-analysis guard, same category as test_menu_js_onclick_
contract.py and test_template_js_id_contract.py).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THANKS_HTML = ROOT / "app" / "web" / "templates" / "customer" / "thanks.html"

_NON_CLEARING_STATES = ("DRAFT", "CONFIRMED", "PAYMENT_PENDING", "CANCELLED")


def test_thanks_html_present() -> None:
    assert THANKS_HTML.exists()


def test_cart_clear_script_present_and_nonced() -> None:
    text = THANKS_HTML.read_text(encoding="utf-8")
    assert "localStorage.setItem('cart', '[]')" in text
    assert 'nonce="{{ csp_nonce }}"' in text, (
        "inline <script> must carry the CSP nonce or it will be blocked "
        "by CSPMiddleware in production"
    )


def test_cart_clear_gated_on_non_terminal_pending_states() -> None:
    """The clear must be wrapped in a condition that excludes every state
    where the payment hasn't genuinely completed."""
    text = THANKS_HTML.read_text(encoding="utf-8")
    clear_idx = text.index("localStorage.setItem('cart', '[]')")
    # Find the nearest {% if ... %} block opener before the clear call.
    if_idx = text.rindex("{% if", 0, clear_idx)
    condition = text[if_idx:clear_idx]
    for state in _NON_CLEARING_STATES:
        assert f'"{state}"' in condition, (
            f"cart-clear guard must exclude order.state == {state!r} — "
            "clearing while payment is still pending (or failed/cancelled) "
            "strands the customer with an empty cart and no completed order"
        )
    assert "not in" in condition
