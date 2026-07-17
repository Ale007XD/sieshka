-- migrations/007_schedule_windows.sql — M7 schedule windows override data.
--
-- ScheduleWindow is the runtime-mutable operational contour for the morning /
-- evening service windows. An admin may change these via natural-language
-- instruction at runtime (sprint_m7_schedule_agent); the governed write path
-- there is the ONLY place allowed to mutate these rows (same discipline as
-- zones / menu).
--
-- SEEDING CONVENTION: this migration seeds the two rows once from the CURRENT
-- app/config.py defaults at sprint time:
--   * MENU_MORNING_END_HOUR = 16  (local hour morning switches to evening)
--   * morning window  = 00:00 .. 16:00
--   * evening window  = 16:00 .. 23:59:59  (23:59:59 = end-of-day sentinel)
-- After the one-time seed, config.py values are NOT re-read; the DB rows are
-- the source of truth for the live windows.
--
-- start_time / end_time are TIME WITHOUT TIME ZONE, local to MENU_TIMEZONE.

CREATE TABLE IF NOT EXISTS schedule_windows (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    period     VARCHAR(16) NOT NULL CHECK (period IN ('morning', 'evening')),
    start_time TIME WITHOUT TIME ZONE NOT NULL,
    end_time   TIME WITHOUT TIME ZONE NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_schedule_windows_period UNIQUE (period)
);

CREATE INDEX IF NOT EXISTS idx_schedule_windows_active ON schedule_windows(is_active);

INSERT INTO schedule_windows (period, start_time, end_time, is_active)
VALUES ('morning', '00:00:00', '16:00:00', TRUE)
ON CONFLICT (period) DO NOTHING;

INSERT INTO schedule_windows (period, start_time, end_time, is_active)
VALUES ('evening', '16:00:00', '23:59:59', TRUE)
ON CONFLICT (period) DO NOTHING;
