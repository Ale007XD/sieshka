-- migrations/021_staff_zalo_user_id.sql — sprint_zalo_staff_column.
--
-- Adds a third messenger-identity column to staff, alongside max_user_id
-- (017_staff.sql) and telegram_user_id (same file). Same rationale as those
-- two: nullable + independently unique (partial index, NULLs excluded) — a
-- staff row may have any subset of {max_user_id, telegram_user_id,
-- zalo_user_id} populated, e.g. a courier onboarded only on Zalo never
-- touches the other two columns.
--
-- Type differs from max_user_id/telegram_user_id (BIGINT): Zalo Mini App
-- issues an opaque per-app string identifier for a user, not a guaranteed
-- numeric value in BIGINT range — VARCHAR(64) mirrors the plan's original
-- column sizing, not a numeric type like the other two messenger columns.

ALTER TABLE staff ADD COLUMN IF NOT EXISTS zalo_user_id VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_zalo_user_id
    ON staff (zalo_user_id) WHERE zalo_user_id IS NOT NULL;
