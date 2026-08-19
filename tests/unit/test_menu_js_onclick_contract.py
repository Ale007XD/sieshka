"""tests/unit/test_menu_js_onclick_contract.py — inline-onclick quoting guard.

sprint_fix_addtocart_syntax_error (2026-08-19): menu.js's default add-to-cart
button used to render

    onclick="addToCartWithQty(${JSON.stringify(productId)}, ...)"

JSON.stringify() wraps its result in double quotes — the SAME quote
character HTML uses to delimit the onclick="" attribute value. The browser
terminates the attribute at the first embedded quote (right after the
literal text "addToCartWithQty("), so everything after that point spills
out as unparsable markup. Reported live as:

    Uncaught SyntaxError: Unexpected end of input (at (index):1:18)

(1:18 is exactly len('addToCartWithQty(') — confirms the attribute was cut
there.) This broke adding ANY product to the cart from the storefront.

The fix removed the inline onclick entirely: setupProductEventDelegation()
already handles .btn-add-to-cart clicks via event delegation, reading
data-product-id/data-price/data-name/data-lead-time-minutes from the
closest .product-card ancestor. No inline handler is needed at all.

This test is a static guard, not a browser/DOM test — it scans the JS
source so the anti-pattern (double-quoted JS-attribute value built with
JSON.stringify) can never silently resurface in a template-string that
lands inside another double-quoted HTML attribute.

No Docker / DB required: it only scans source files on disk.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MENU_JS = ROOT / "app" / "web" / "static" / "js" / "menu.js"

# Matches: onclick="...JSON.stringify(...)..."  (double-quoted attribute
# whose value embeds a JSON.stringify() call — the exact collision that
# broke add-to-cart). Single-quoted onclick="'...'" with JSON.stringify
# would NOT collide and is intentionally not flagged.
_BROKEN_ONCLICK_PATTERN = re.compile(
    r'onclick="[^"]*JSON\.stringify\([^"]*"'
)


def test_menu_js_present() -> None:
    assert MENU_JS.exists()


def test_no_json_stringify_inside_double_quoted_onclick() -> None:
    """A JSON.stringify() call must never sit inside a double-quoted
    onclick="" attribute value — the embedded double quotes truncate the
    attribute at parse time (2026-08-19 live incident)."""
    text = MENU_JS.read_text(encoding="utf-8")
    matches = _BROKEN_ONCLICK_PATTERN.findall(text)
    assert not matches, (
        "menu.js contains a double-quoted onclick=\"\" attribute whose "
        "value embeds JSON.stringify() — this truncates the HTML attribute "
        f"at the first embedded quote and breaks the handler entirely: {matches}"
    )


def test_add_to_cart_button_has_no_inline_onclick() -> None:
    """Default add-to-cart button relies on event delegation
    (setupProductEventDelegation), not an inline handler — regression
    guard for the specific button that broke."""
    text = MENU_JS.read_text(encoding="utf-8")
    assert 'class="btn btn-brand btn-add-to-cart btn-sm w-100"' in text
    # The add-to-cart button markup block must not carry onclick at all.
    button_block_re = re.compile(
        r'<button class="btn btn-brand btn-add-to-cart btn-sm w-100"[^>]*>',
        re.DOTALL,
    )
    m = button_block_re.search(text)
    assert m is not None, "default add-to-cart button markup not found"
    assert "onclick" not in m.group(0), (
        "default add-to-cart button must not have an inline onclick — "
        "setupProductEventDelegation() already handles .btn-add-to-cart "
        "clicks via delegation"
    )


def test_event_delegation_handles_add_to_cart() -> None:
    """setupProductEventDelegation() must exist and read the dataset
    fields the delegation relies on (data-product-id/-price/-name/
    -lead-time-minutes on the closest .product-card)."""
    text = MENU_JS.read_text(encoding="utf-8")
    assert "function setupProductEventDelegation()" in text
    assert "addToCartWithQty(productId, price, name, leadTimeMinutes)" in text
