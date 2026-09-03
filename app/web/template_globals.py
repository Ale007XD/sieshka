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

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import settings


def local_dt(value: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    """Render a tz-aware (or naive-UTC) datetime in MENU_TIMEZONE.

    gap-analysis-review-session (2026-08-31): admin kitchen/orders boards had
    no placement time at all. asyncpg returns TIMESTAMPTZ columns as
    tz-aware UTC datetimes — showing that raw would silently be wrong by the
    same ~7h VPS(UTC)-vs-business(Asia/Ho_Chi_Minh) gap already fixed once
    for effective_date comparisons (CONSTRAINTS.md, 2026-07-20). Naive
    datetimes (e.g. from a test fixture that didn't set tzinfo) are assumed
    UTC rather than raising, matching asyncpg's actual behavior.
    """
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(settings.MENU_TIMEZONE)).strftime(fmt)


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
    templates.env.filters["local_dt"] = local_dt
