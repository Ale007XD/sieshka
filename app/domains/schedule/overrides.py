"""app/domains/schedule/overrides.py — DB-backed schedule windows.

ScheduleWindow is reference/override data describing the morning and evening
service windows. It is the runtime-mutable operational contour that an admin
may change via natural-language instruction (sprint_m7_schedule_agent).

It is intentionally a pydantic model (not a SQLAlchemy ORM entity) — mirroring
the DeliveryZone reference-data pattern in app/domains/delivery/zones.py. The
service layer reads the current rows via raw SQL (the governed write path for
mutations lives exclusively in sprint_m7_schedule_agent.apply_schedule_command).

start_time / end_time are local to the project timezone (app/config.py
MENU_TIMEZONE) and stored as TIME WITHOUT TIME ZONE.
"""
from __future__ import annotations

from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

SchedulePeriod = Literal["morning", "evening"]


class ScheduleWindow(BaseModel):
    id: UUID
    period: SchedulePeriod
    start_time: time
    end_time: time
    is_active: bool = True
    # NULL = permanent default row (exactly one per period). A date = a
    # one-day-only override (exactly one per (period, date)) that expires by
    # construction on read — get_menu_window_context() only reads the row for
    # CURRENT_DATE (or the permanent NULL row), so yesterday's override is
    # never selected. See migrations/008_schedule_windows_effective_date.sql.
    effective_date: date | None = None
