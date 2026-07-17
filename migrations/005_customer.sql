-- migrations/005_customer.sql — M7 customers table.
--
-- Customer is the only persisted end-user identity. It is created exclusively
-- by app.services.customer_service.find_or_create_by_phone(), which normalizes
-- the phone to canonical "+7XXXXXXXXXX" BEFORE lookup, so "+7 (999) 123-45-67"
-- and "+79991234567" resolve to the same row. The UNIQUE index on phone is what
-- makes find-or-create safe under concurrency for the canonical form.
--
-- SCOPE BOUNDARY (sprint_m7_customer_domain OPEN QUESTION): customer identity
-- resolution is a deliberate, named exception to the governed-Tool write pattern.
-- It is a dedup lookup, not a state transition — there is no FSM/state to govern
-- and no decision to interpret — so find_or_create_by_phone() runs as a bare
-- service call and produces no nano-vm Trace/ExecutionReceipt. This mirrors the
-- CSV-import exception's spirit (structured input, nothing to interpret). If this
-- boundary is later revisited, normalize_phone() is already a pure function that
-- can be lifted into a GovernedToolExecutor TOOL step without change.

CREATE TABLE IF NOT EXISTS customers (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    phone       VARCHAR(32) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_phone ON customers(phone);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
