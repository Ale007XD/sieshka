"""app/web/template_globals.py — Jinja2 globals shared between the real app
and any test harness that builds its own Jinja2Templates instance.

sprint_static_cache_busting (2026-08-18): asset_version() must be available
wherever customer/shop_base.html (and templates extending it) get rendered.
Several integration test files build a standalone FastAPI()+Jinja2Templates
pair instead of importing app.main.app (Docker-free rendering-only tests —
see test_customer_routes.py's docstring) — registering globals only on
app.main's module-level `app` instance leaves those harnesses without them,
which is exactly the kind of test/production drift this whole incident was
already about. One shared registration function, called from both sides,
avoids that drift recurring the next time a global is added.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates


def asset_version(static_dir: Path, rel_path: str) -> int:
    """mtime-based cache-busting query param for /static/ assets.

    See app/main.py's call site for the full incident writeup (Telegram
    WebView serving a stale cached cart.js after a deploy). mtime is enough
    at this project's size — changes on every real edit, stable otherwise —
    no build pipeline / content-hash infra needed.
    """
    path = static_dir / rel_path
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def register_template_globals(templates: Jinja2Templates, static_dir: Path) -> None:
    """Wire asset_version() (and any future shared global) into `templates`.

    Call this on every Jinja2Templates instance that renders
    customer/shop_base.html or a template extending it — the real app
    (app/main.py) and any standalone test harness alike.
    """
    templates.env.globals["asset_version"] = lambda rel_path: asset_version(
        static_dir, rel_path
    )
