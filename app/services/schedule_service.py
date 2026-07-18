"""app/services/schedule_service.py — schedule window context for menus/checkout.

Reads the CURRENT schedule_windows rows (the DB-backed override, seeded once
from app/config.py defaults) and layers the morning/evening + preorder business
logic on top.

The OPEN/CLOSING_SOON/CLOSED state comes from the EXISTING BusinessScheduleFSM
(see app/domains/schedule/fsm.py). Per that FSM's own constraint ("graph-only —
no business rules inside"), the time-window / preorder logic lives HERE in the
service layer, not in the FSM graph. The FSM is consulted, not modified.
"""
from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import async_session_factory
from app.domains.schedule.fsm import BusinessScheduleState
from app.domains.schedule.overrides import SchedulePeriod, ScheduleWindow

_DEFAULT_TZ = ZoneInfo("UTC")
_EOD_SENTINEL = datetime.time(23, 59, 59)


def _menu_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.MENU_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return _DEFAULT_TZ


class ScheduleService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    ) -> None:
        self._session_factory = session_factory

    async def get_menu_window_context(
        self,
        current_state: BusinessScheduleState = BusinessScheduleState.OPEN,
    ) -> dict[str, Any]:
        """Build the schedule context consumed by closed.html / checkout.html.

        Returns a dict with exactly:
          is_open, is_evening_preorder,
          morning_start, morning_end, evening_start, evening_end,
          preorder_info: {"is_preorder", "time_until", "opens_at"} | None
        """
        windows = await self._load_windows()
        morning = windows.get("morning")
        evening = windows.get("evening")

        morning_start = morning.start_time if morning else None
        morning_end = morning.end_time if morning else None
        evening_start = evening.start_time if evening else None
        evening_end = evening.end_time if evening else None

        now = datetime.datetime.now(_menu_timezone())
        now_time = now.time()

        fsm_open = current_state == BusinessScheduleState.OPEN

        # A window is "open right now" only if it is active and the local clock
        # falls within [start, end]. The evening end is an inclusive sentinel.
        def _window_open(win: ScheduleWindow | None) -> bool:
            if win is None or not win.is_active:
                return False
            if win.end_time == _EOD_SENTINEL:
                return win.start_time <= now_time
            return win.start_time <= now_time <= win.end_time

        in_morning = _window_open(morning)
        in_evening = _window_open(evening)
        is_open = fsm_open and (in_morning or in_evening)

        # Evening preorder: FSM is NOT open, but there is an active evening window
        # scheduled for later today — accept preorders that open when evening starts.
        is_evening_preorder = False
        preorder_info: dict[str, Any] | None = None

        if (
            not is_open
            and evening is not None
            and evening.is_active
            and now_time < evening.start_time
        ):
            is_evening_preorder = True
            open_dt = datetime.datetime.combine(now.date(), evening.start_time).replace(
                tzinfo=now.tzinfo
            )
            delta = open_dt - now
            preorder_info = {
                "is_preorder": True,
                "time_until": int(delta.total_seconds()),
                "opens_at": evening.start_time.isoformat(),
            }

        return {
            "is_open": is_open,
            "is_evening_preorder": is_evening_preorder,
            "morning_start": morning_start.isoformat() if morning_start else None,
            "morning_end": morning_end.isoformat() if morning_end else None,
            "evening_start": evening_start.isoformat() if evening_start else None,
            "evening_end": evening_end.isoformat() if evening_end else None,
            "preorder_info": preorder_info,
        }

    async def _load_windows(self) -> dict[SchedulePeriod, ScheduleWindow]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, period, start_time, end_time, is_active, "
                    "effective_date "
                    "FROM schedule_windows "
                    "WHERE period IN ('morning', 'evening') "
                    "AND (effective_date = CURRENT_DATE OR effective_date IS NULL) "
                    "ORDER BY period, effective_date NULLS LAST"
                )
            )
            rows = result.fetchall()

        windows: dict[SchedulePeriod, ScheduleWindow] = {}
        for row in rows:
            period = row._mapping["period"]
            if period not in ("morning", "evening"):
                continue
            # effective_date NULLS LAST orders a day-override (non-null) ahead
            # of the permanent (null) row for the same period, so the first
            # row per period is the one currently in effect. A yesterday
            # override is never returned because the WHERE clause excludes it.
            if period in windows:
                continue
            windows[period] = ScheduleWindow(
                id=row._mapping["id"],
                period=period,
                start_time=row._mapping["start_time"],
                end_time=row._mapping["end_time"],
                is_active=row._mapping["is_active"],
                effective_date=row._mapping["effective_date"],
            )
        return windows

    async def get_admin_windows(
        self,
    ) -> dict[str, dict[str, Any]]:
        """Return, per period, BOTH the permanent default and today's override.

        An admin must see whether a today-override is silently in effect, not
        just the permanent default — otherwise they'd be misled about what
        customers are currently seeing. Keys: "morning"/"evening" →
        {"permanent": ScheduleWindow | None, "today_override": ScheduleWindow | None}.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, period, start_time, end_time, is_active, "
                    "effective_date "
                    "FROM schedule_windows "
                    "WHERE period IN ('morning', 'evening') "
                    "AND (effective_date = CURRENT_DATE OR effective_date IS NULL) "
                    "ORDER BY period, effective_date NULLS LAST"
                )
            )
            rows = result.fetchall()

        out: dict[str, dict[str, Any]] = {
            "morning": {"permanent": None, "today_override": None},
            "evening": {"permanent": None, "today_override": None},
        }
        for row in rows:
            period = row._mapping["period"]
            if period not in out:
                continue
            win = ScheduleWindow(
                id=row._mapping["id"],
                period=period,
                start_time=row._mapping["start_time"],
                end_time=row._mapping["end_time"],
                is_active=row._mapping["is_active"],
                effective_date=row._mapping["effective_date"],
            )
            if row._mapping["effective_date"] is None:
                out[period]["permanent"] = win
            else:
                out[period]["today_override"] = win
        return out
