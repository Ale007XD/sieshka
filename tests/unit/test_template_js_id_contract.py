"""tests/unit/test_template_js_id_contract.py — DOM-ID contract between JS and templates.

sprint_m7_static_assets_smoke deliverable #2: the customer templates MUST
provide every element id that cart.js / menu.js reference. The JS files are
the real received assets and are the fixed contract — the templates must match
the JS, not the other way around. This test fails the build if any referenced
id is missing, so a ported template can never silently drop a contract id.

No Docker / DB required: it only scans source files on disk.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "app" / "web" / "static" / "js"
TEMPLATES_DIR = ROOT / "app" / "web" / "templates" / "customer"

_ID_IN_JS = re.compile(r'getElementById\(\s*["\']([^"\']+)["\']\s*\)')
_QSELECTOR_ID_IN_JS = re.compile(r'querySelector\(\s*["\']#([A-Za-z0-9_-]+)["\']\s*\)')


def _collect_referenced_ids() -> dict[str, set[str]]:
    """Map each JS file to the set of element ids it references."""
    referenced: dict[str, set[str]] = {}
    for js_path in JS_DIR.glob("*.js"):
        text = js_path.read_text(encoding="utf-8")
        ids: set[str] = set()
        for m in _ID_IN_JS.finditer(text):
            ids.add(m.group(1))
        for m in _QSELECTOR_ID_IN_JS.finditer(text):
            ids.add(m.group(1))
        if ids:
            referenced[js_path.name] = ids
    return referenced


def _collect_template_ids() -> set[str]:
    """All ids declared across the customer templates (Jinja blocks resolved)."""
    ids: set[str] = set()
    id_re = re.compile(r'id=["\']([A-Za-z0-9_-]+)["\']')
    for html_path in TEMPLATES_DIR.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8")
        for m in id_re.finditer(text):
            ids.add(m.group(1))
    return ids


def test_js_files_present() -> None:
    assert (JS_DIR / "cart.js").exists()
    assert (JS_DIR / "menu.js").exists()


def test_all_template_ids_resolved() -> None:
    """cart.js/menu.js must not reference any id absent from the templates."""
    referenced = _collect_referenced_ids()
    assert referenced, "expected to find id references in the customer JS"
    template_ids = _collect_template_ids()
    assert template_ids, "expected to find ids declared in customer templates"

    missing: list[tuple[str, str]] = []
    for js_file, ids in referenced.items():
        for ref_id in sorted(ids):
            if ref_id not in template_ids:
                missing.append((js_file, ref_id))

    assert not missing, (
        "customer templates are missing element ids referenced by JS "
        f"(contract violation): {missing}"
    )


def test_contract_ids_explicitly_present() -> None:
    """The sprint's named contract ids all resolve (regression guard)."""
    referenced = _collect_referenced_ids()
    all_referenced: set[str] = set().union(*referenced.values())
    for contract_id in (
        "menu-root",
        "menu-status",
        "cart-root",
        "cart-summary",
        "cart-count",
        "toast-root",
        "checkout-form",
        "checkout-submit",
        "checkout-error",
        "yookassa-widget",
        "f-zone",
        "f-name",
        "f-phone",
        "f-address",
        "f-comment",
    ):
        assert contract_id in all_referenced, (
            f"{contract_id} is referenced by JS but not collected"
        )
