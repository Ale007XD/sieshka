"""tests/unit/test_asset_version.py — sprint_static_cache_busting (2026-08-18).

Root-cause incident: Telegram's in-app WebView served a stale cached copy
of cart.js after a deploy that changed it (no cache-busting existed on any
static asset reference), so a shipped server-side fix never actually ran
client-side. asset_version() fixes this by keying the query string off each
file's own mtime.

Moved to app.web.template_globals (from app.main directly) after
test_customer_routes.py's own independent Jinja2Templates instance turned
up needing the same registration — see that module's docstring.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.web.template_globals import asset_version, register_template_globals

_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "web" / "static"


class TestAssetVersion:
    def test_returns_int_mtime_for_real_file(self) -> None:
        version = asset_version(_STATIC_DIR, "js/cart.js")
        assert isinstance(version, int)
        assert version > 0

    def test_matches_actual_file_mtime(self) -> None:
        expected = int((_STATIC_DIR / "js/cart.js").stat().st_mtime)
        assert asset_version(_STATIC_DIR, "js/cart.js") == expected

    def test_missing_file_returns_zero_not_raise(self) -> None:
        """Must never 500 template rendering over a missing/renamed asset —
        worst case is a cache-buster of 0 (browser caches normally, same as
        before this sprint), not a broken page."""
        assert asset_version(_STATIC_DIR, "js/does-not-exist-xyz.js") == 0

    def test_different_files_can_have_different_versions(self) -> None:
        # Not a strict inequality assertion (files could coincidentally share
        # an mtime) — just confirms both resolve independently without error.
        cart_v = asset_version(_STATIC_DIR, "js/cart.js")
        theme_v = asset_version(_STATIC_DIR, "css/theme.css")
        assert isinstance(cart_v, int)
        assert isinstance(theme_v, int)


class TestRegisterTemplateGlobals:
    def test_registers_callable_global(self) -> None:
        templates = Jinja2Templates(directory=str(_STATIC_DIR.parent / "templates"))
        register_template_globals(templates, _STATIC_DIR)
        assert "asset_version" in templates.env.globals
        assert callable(templates.env.globals["asset_version"])

    def test_registered_global_resolves_same_as_direct_call(self) -> None:
        templates = Jinja2Templates(directory=str(_STATIC_DIR.parent / "templates"))
        register_template_globals(templates, _STATIC_DIR)
        via_global = templates.env.globals["asset_version"]("js/cart.js")
        via_direct = asset_version(_STATIC_DIR, "js/cart.js")
        assert via_global == via_direct
