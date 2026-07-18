-- migrations/008_schedule_windows_effective_date.sql — sprint_m7_schedule_agent.
--
-- Variant B: a ScheduleWindow may now be either
--   * a PERMANENT default (effective_date IS NULL) — exactly one per period, OR
--   * a one-day-only OVERRIDE (effective_date = a DATE) — exactly one per
--     (period, effective_date). Overrides "expire by construction" on read:
--     get_menu_window_context() only SELECTs the row for CURRENT_DATE (or the
--     permanent NULL row), so yesterday's override is simply never read.
--
-- The original migration 007 shipped CONSTRAINT uq_schedule_windows_period
-- UNIQUE (period), which physically forbids a second row for the same period.
-- That shape is dropped here and replaced by TWO partial unique indexes that
-- each enforce exactly one row per its slice:
--   * uq_schedule_windows_permanent    — one NULL (permanent) row per period
--   * uq_schedule_windows_today_override — one dated row per (period, date)
--
-- A single plain UNIQUE(period, effective_date) is deliberately NOT used:
-- Postgres treats NULL as distinct from NULL in a unique constraint, so once
-- effective_date exists the permanent (NULL) rows would no longer be forced
-- unique-per-period — two permanent rows for "morning" could both be NULL and
-- both pass a plain unique index. The two partial indexes avoid that trap.

ALTER TABLE schedule_windows ADD COLUMN effective_date DATE;

ALTER TABLE schedule_windows DROP CONSTRAINT uq_schedule_windows_period;

CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_windows_permanent
    ON schedule_windows (period) WHERE effective_date IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_windows_today_override
    ON schedule_windows (period, effective_date) WHERE effective_date IS NOT NULL;
