"""tests/unit/test_cart_js_checkout_flow.py — cart.js checkout submit contract.

sprint_fix_online_payment_funnel (2026-08-19), two live-reported bugs fixed
together since they're in the same code block:

#5 "Корзина пуста" on retry — cart.js used to clear localStorage['cart']
   immediately after the order+payment-link was created, before the
   customer had actually paid. If they abandoned the YooKassa page and
   came back, the cart was already wiped but no order was ever completed
   — "Оплатить и заказать заново" saw an empty cart while the storefront
   icon/badge still showed the (stale, uncleared-in-DOM) item. Fix: for
   the online-payment branch (confirmation_url present), the cart is left
   alone here; it's cleared from /thanks/{order_id} instead, gated on the
   order having actually reached a paid state (see
   test_thanks_page_cart_clear_contract.py). The cash branch (no
   confirmation_url) still clears immediately — that order is confirmed
   synchronously right there, no external redirect involved.

#2 QR/redirect opens in a new window inside MAX — cart.js already branched
   on window.Telegram.WebApp.openLink() for Telegram's Mini App bridge but
   fell through to window.location.href for everything else, including
   MAX — which navigates the MAX WebView itself to YooKassa instead of the
   system browser. MAX's bridge (window.WebApp, loaded via
   st.max.ru/js/max-web-app.js in shop_base.html) exposes the identical
   openLink() primitive. Fix: added a matching branch before the
   window.location.href fallback.

No Docker / DB required: it only scans source files on disk.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CART_JS = ROOT / "app" / "web" / "static" / "js" / "cart.js"


def _submit_handler_block() -> str:
    """Return the source slice from the fetch('/api/orders') call through
    the end of the response.ok/data.ok success branch, so assertions stay
    scoped to the checkout submit handler and can't accidentally match an
    unrelated localStorage.setItem('cart', ...) call elsewhere in the file."""
    text = CART_JS.read_text(encoding="utf-8")
    start = text.index("await fetch('/api/orders'")
    # The success branch closes with the outer `} else {` for the
    # !response.ok/!data.ok error branch — slice up to that boundary.
    end = text.index("const errorMsg = data.detail", start)
    return text[start:end]


def test_cart_js_present() -> None:
    assert CART_JS.exists()


def test_online_payment_branch_does_not_clear_cart() -> None:
    block = _submit_handler_block()
    confirmation_branch_start = block.index("if (data.confirmation_url)")
    confirmation_branch_end = block.index("} else {", confirmation_branch_start)
    confirmation_branch = block[confirmation_branch_start:confirmation_branch_end]
    assert "localStorage.setItem('cart'" not in confirmation_branch, (
        "cart must not be cleared while the customer merely holds a "
        "payment link — clearing belongs on /thanks/{order_id} after the "
        "payment actually succeeds"
    )


def test_cash_branch_still_clears_cart() -> None:
    block = _submit_handler_block()
    confirmation_branch_start = block.index("if (data.confirmation_url)")
    cash_branch = block[block.index("} else {", confirmation_branch_start):]
    assert "localStorage.setItem('cart', '[]')" in cash_branch, (
        "cash path confirms the order synchronously right here — cart "
        "clearing must remain immediate for that path"
    )


def test_max_webview_uses_own_bridge_openlink() -> None:
    """window.WebApp.openLink must be tried before falling back to
    window.location.href, mirroring the existing Telegram branch —
    otherwise MAX's own WebView navigates itself to YooKassa instead of
    opening the system browser."""
    block = _submit_handler_block()
    telegram_idx = block.index("window.Telegram.WebApp.openLink")
    max_idx = block.index("window.WebApp.openLink")
    fallback_idx = block.index("window.location.href = data.confirmation_url")
    assert telegram_idx < max_idx < fallback_idx, (
        "expected order: Telegram bridge branch, then MAX bridge branch, "
        "then window.location.href fallback"
    )


def test_max_bridge_check_is_null_safe() -> None:
    """window.WebApp is only present inside MAX's own WebView — the check
    must guard both window.WebApp and .openLink, same pattern as the
    existing Telegram check, or this throws on every non-MAX browser."""
    block = _submit_handler_block()
    assert re.search(
        r"window\.WebApp\s*&&\s*window\.WebApp\.openLink", block,
    ), "window.WebApp.openLink access must be guarded with window.WebApp &&"
