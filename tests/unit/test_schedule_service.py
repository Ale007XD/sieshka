"""tests/unit/test_schedule_service.py — schedule window context logic."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.domains.schedule.fsm import BusinessScheduleState
from app.services.schedule_service import ScheduleService, _menu_timezone


def _window_row(
    period: str,
    start: time,
    end: time,
    is_active: bool = True,
    effective_date: Any = None,
) -> MagicMock:
    row = MagicMock()
    row._mapping = {
        "id": uuid4(),
        "period": period,
        "start_time": start,
        "end_time": end,
        "is_active": is_active,
        "effective_date": effective_date,
    }
    return row


def _session(rows: list[MagicMock]) -> AsyncMock:
    session = AsyncMock()

    async def execute_side_effect(stmt: Any, params: dict | None = None) -> MagicMock:
        result = MagicMock()
        result.fetchall.return_value = rows
        return result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


@asynccontextmanager
async def _asession(session: AsyncMock) -> AsyncGenerator[AsyncMock, None]:
    yield session


def _make_service(rows: list[MagicMock]) -> ScheduleService:
    svc = ScheduleService()
    svc._session_factory = lambda: _asession(session=_session(rows))  # type: ignore[assignment]
    return svc


class TestMenuWindowContextOpen:
    async def test_open_during_morning_window(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),
                _window_row("evening", time(16, 0), time(23, 59, 59)),
            ]
        )
        now = datetime(2026, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context()

        assert ctx["is_open"] is True
        assert ctx["is_evening_preorder"] is False
        assert ctx["preorder_info"] is None
        assert ctx["morning_start"] == "00:00:00"
        assert ctx["evening_start"] == "16:00:00"

    async def test_open_during_evening_window(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),
                _window_row("evening", time(16, 0), time(23, 59, 59)),
            ]
        )
        now = datetime(2026, 1, 1, 20, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context()

        assert ctx["is_open"] is True
        assert ctx["preorder_info"] is None


class TestMenuWindowContextClosed:
    async def test_closed_when_fsm_closed(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),
                _window_row("evening", time(16, 0), time(23, 59, 59)),
            ]
        )
        now = datetime(2026, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context(
                current_state=BusinessScheduleState.CLOSED
            )

        assert ctx["is_open"] is False

    async def test_closed_when_outside_all_windows(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(8, 0), time(11, 0)),
                _window_row("evening", time(17, 0), time(23, 59, 59)),
            ]
        )
        now = datetime(2026, 1, 1, 14, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context()

        assert ctx["is_open"] is False


class TestEveningPreorder:
    async def test_evening_preorder_before_evening_window(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),
                _window_row("evening", time(16, 0), time(23, 59, 59)),
            ]
        )
        now = datetime(2026, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context(
                current_state=BusinessScheduleState.CLOSED
            )

        assert ctx["is_evening_preorder"] is True
        assert ctx["preorder_info"] is not None
        assert ctx["preorder_info"]["is_preorder"] is True
        assert ctx["preorder_info"]["opens_at"] == "16:00:00"
        assert ctx["preorder_info"]["time_until"] == 6 * 3600

    async def test_no_preorder_when_evening_window_inactive(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),
                _window_row("evening", time(16, 0), time(23, 59, 59), is_active=False),
            ]
        )
        now = datetime(2026, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context(
                current_state=BusinessScheduleState.CLOSED
            )

        assert ctx["is_evening_preorder"] is False
        assert ctx["preorder_info"] is None


class TestLoadWindows:
    async def test_only_active_windows_open(self) -> None:
        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0), is_active=False),
                _window_row("evening", time(16, 0), time(23, 59, 59)),
            ]
        )
        now = datetime(2026, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            ctx = await svc.get_menu_window_context()

        assert ctx["is_open"] is False


def _fixed_datetime(fixed: datetime) -> Any:
    import types

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, _tz: Any = None) -> datetime:
            return fixed

    return types.SimpleNamespace(datetime=_FixedDateTime)


class TestLoadWindowsEffectiveDate:
    """Read-branch coverage for the effective_date introduction (008).

    These exercise the WHERE/ORDER logic that prefers a today-override over the
    permanent default, and that a stale (yesterday) override is never picked up.
    """

    async def test_today_override_preferred_over_permanent(self) -> None:
        from datetime import date

        svc = _make_service(
            [
                # Mirrors the SQL "ORDER BY effective_date NULLS LAST": the
                # non-null (today) row sorts BEFORE the permanent (NULL) row.
                _window_row(
                    "morning", time(8, 0), time(12, 0),
                    effective_date=date(2099, 1, 1),
                ),  # today override (fixed "now" is 2099-01-01)
                _window_row("morning", time(0, 0), time(16, 0)),  # permanent
            ]
        )
        now = datetime(2099, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            windows = await svc._load_windows()

        assert windows["morning"].start_time == time(8, 0)
        assert windows["morning"].end_time == time(12, 0)

    async def test_yesterday_override_falls_through_to_permanent(self) -> None:
        from datetime import date

        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),  # permanent
                _window_row(
                    "morning", time(8, 0), time(12, 0),
                    effective_date=date(2099, 1, 1),
                ),  # yesterday override (fixed "now" is 2099-01-02)
            ]
        )
        now = datetime(2099, 1, 2, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            windows = await svc._load_windows()

        # The stale override must be ignored; the permanent row is in effect.
        assert windows["morning"].start_time == time(0, 0)
        assert windows["morning"].end_time == time(16, 0)

    async def test_get_admin_windows_shows_both_slices(self) -> None:
        from datetime import date

        svc = _make_service(
            [
                _window_row("morning", time(0, 0), time(16, 0)),
                _window_row("evening", time(16, 0), time(23, 59, 59)),
                _window_row(
                    "morning", time(8, 0), time(12, 0),
                    effective_date=date(2099, 1, 1),
                ),
            ]
        )
        now = datetime(2099, 1, 1, 10, 0, tzinfo=_menu_timezone())
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("app.services.schedule_service.datetime", _fixed_datetime(now))
            out = await svc.get_admin_windows()

        assert out["morning"]["permanent"] is not None
        assert out["morning"]["today_override"] is not None
        assert out["evening"]["permanent"] is not None
        assert out["evening"]["today_override"] is None
