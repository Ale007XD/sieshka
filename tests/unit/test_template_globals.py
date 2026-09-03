"""tests/unit/test_template_globals.py — local_dt filter.

gap-analysis-review-session (2026-08-31): admin kitchen/orders boards gained
a placement-time display via OrderRead.created_at + this filter. Covers the
same class of tz bug already fixed once for effective_date comparisons
(CONSTRAINTS.md, 2026-07-20) — never show raw UTC to staff.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.web.template_globals import local_dt


def test_local_dt_none_returns_dash() -> None:
    assert local_dt(None) == "—"


def test_local_dt_converts_utc_to_menu_timezone() -> None:
    # 2026-08-31 05:00 UTC == 2026-08-31 12:00 Asia/Ho_Chi_Minh (UTC+7)
    value = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)
    with patch("app.web.template_globals.settings") as mock_settings:
        mock_settings.MENU_TIMEZONE = "Asia/Ho_Chi_Minh"
        assert local_dt(value) == "31.08 12:00"


def test_local_dt_naive_datetime_assumed_utc() -> None:
    """asyncpg TIMESTAMPTZ columns are always tz-aware in practice, but a
    naive datetime (e.g. from a test fixture) must not raise — treat it as
    UTC rather than crashing on astimezone()."""
    value = datetime(2026, 8, 31, 5, 0)  # naive
    with patch("app.web.template_globals.settings") as mock_settings:
        mock_settings.MENU_TIMEZONE = "Asia/Ho_Chi_Minh"
        assert local_dt(value) == "31.08 12:00"


def test_local_dt_custom_format() -> None:
    value = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)
    with patch("app.web.template_globals.settings") as mock_settings:
        mock_settings.MENU_TIMEZONE = "UTC"
        assert local_dt(value, fmt="%Y-%m-%d") == "2026-08-31"
