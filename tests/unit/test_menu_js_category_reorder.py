"""tests/unit/test_menu_js_category_reorder.py — category sort-order DOM guard.

Live report (2026-08-29): a category with sort=0 (explicitly first) rendered
at the very bottom of the storefront after switching fulfillment method
(delivery -> pickup).

Root cause: renderMenu()/populateCategoryButtons() only ever *create*
missing category sections/buttons with a bare appendChild() when a category
appears for the first time under a newly selected method (e.g. a
pickup-only category absent from the prior delivery load). appendChild()
puts the new node at the END of its container — the backend already
returns categories ORDER BY sort (menu_service.py::_fetch_categories), but
nothing in the JS re-applies that order to the existing DOM. A category
that happens to be "new" for the current method always lands last,
regardless of its `sort` value.

Fix: after the create/update pass, both functions re-walk their already-
sorted input list and re-append() every existing node in that order.
appendChild() on a node already attached to the document MOVES it instead
of duplicating it, so this is a cheap full reorder without rebuilding.

This is a static guard, not a browser/DOM test — it scans the JS source so
the two reorder passes (category sections in #menu-container, category
buttons in #categoryButtons) can't silently regress back to
create-only-append. No Docker/DB/browser required.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MENU_JS = ROOT / "app" / "web" / "static" / "js" / "menu.js"


def _source() -> str:
    return MENU_JS.read_text(encoding="utf-8")


def test_menu_js_present() -> None:
    assert MENU_JS.exists()


def test_render_menu_reorders_category_sections_by_data_order() -> None:
    """renderMenu() must re-walk data.categories and re-append each
    existing section to menuContainer, moving it into sort-order position
    — not just appendChild() on first creation."""
    text = _source()
    render_menu_start = text.index("function renderMenu(data)")
    populate_start = text.index("function populateCategoryButtons(categories)")
    assert populate_start > render_menu_start
    render_menu_body = text[render_menu_start:populate_start]

    reorder_pattern = re.compile(
        r"data\.categories\.forEach\(category\s*=>\s*\{\s*"
        r"const section = document\.getElementById\(`category-\$\{category\.category_id\}`\);\s*"
        r"if \(section\) \{\s*"
        r"menuContainer\.appendChild\(section\);",
        re.DOTALL,
    )
    assert reorder_pattern.search(render_menu_body), (
        "renderMenu() is missing the final reorder pass that re-appends "
        "each existing category section in data.categories order — "
        "without it, a category new to the current method lands after "
        "every pre-existing section regardless of its `sort` value "
        "(live report 2026-08-29, sort=0 category rendered last)"
    )

    # The reorder pass must run unconditionally (after the main forEach,
    # not nested inside the early-return first-render branch), so it also
    # fixes ordering on every subsequent method switch, not just the first
    # time a category shows up.
    reorder_pos = render_menu_body.index("data.categories.forEach(category =>")
    early_return_pos = render_menu_body.index("if (menuContainer && !hasExistingCards)")
    assert reorder_pos > early_return_pos, (
        "the reorder pass must live in the update path (after the "
        "hasExistingCards early-return branch), not only in the "
        "first-render branch"
    )


def test_populate_category_buttons_reorders_by_categories_order() -> None:
    """populateCategoryButtons() must re-walk `categories` and re-append
    each existing button (after re-pinning the 'Все меню' button first) —
    same append-move reorder technique as renderMenu()."""
    text = _source()
    populate_start = text.index("function populateCategoryButtons(categories)")
    next_fn = text.index("function filterByCategory(categoryId)")
    assert next_fn > populate_start
    populate_body = text[populate_start:next_fn]

    assert re.search(
        r"if \(allBtn\)\s*\{\s*container\.appendChild\(allBtn\);\s*\}",
        populate_body,
    ), "'Все меню' button must be re-pinned first before reordering the rest"

    reorder_pattern = re.compile(
        r"categories\.forEach\(cat\s*=>\s*\{\s*"
        r"const btn = container\.querySelector\(`\.category-btn"
        r"\[data-category-id=\"\$\{cat\.category_id\}\"\]`\);\s*"
        r"if \(btn\) \{\s*"
        r"container\.appendChild\(btn\);",
        re.DOTALL,
    )
    assert reorder_pattern.search(populate_body), (
        "populateCategoryButtons() is missing the final reorder pass that "
        "re-appends each existing category button in `categories` order — "
        "without it, a button new to the current method lands after every "
        "pre-existing button regardless of its `sort` value"
    )

    # Reorder must happen after the create-missing-buttons loop, not before
    # (it needs every button to already exist in the DOM to reorder them).
    create_pos = populate_body.index("container.appendChild(btn);")
    reorder_pos = populate_body.rindex("container.appendChild(btn);")
    assert reorder_pos > create_pos, (
        "reorder pass must run after the loop that creates missing buttons"
    )
