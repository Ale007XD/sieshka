-- migrations/017_staff.sql — sprint_staff_table.
--
-- Maps a messenger identity (MAX max_user_id / Telegram telegram_user_id) to a
-- fixed operational role. This is a routing lookup for role_gate() in the
-- upcoming MAX/Telegram channel adapter — it has no FSM/state to govern and no
-- decision to interpret, the same non-goal class as
-- customers.find_or_create_by_phone (migrations/005_customer.sql): it answers
-- "who is allowed to trigger which transition", not "what happened". The
-- transitions it gates (KitchenEvent via KitchenService, OrderEvent via
-- OrderService) remain fully governed and untouched by this table.
--
-- role is a closed set (kitchen/courier/admin), enforced via CHECK — mirrors
-- OrderStatus/KitchenState being closed enums elsewhere in this schema, not a
-- free-form string column.
--
-- Both messenger id columns are nullable and independently unique (partial
-- indexes, NULLs excluded) — a staff row may have only max_user_id, only
-- telegram_user_id, or both, e.g. once the Telegram channel is added later
-- (sprint_channel_adapter_core precedent) without a schema change here.

CREATE TABLE IF NOT EXISTS staff (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name              VARCHAR(255) NOT NULL,
    role              VARCHAR(32) NOT NULL,
    max_user_id       BIGINT,
    telegram_user_id  BIGINT,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT staff_role_check CHECK (role IN ('kitchen', 'courier', 'admin'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_max_user_id
    ON staff (max_user_id) WHERE max_user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_telegram_user_id
    ON staff (telegram_user_id) WHERE telegram_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_staff_role_active ON staff (role, active);