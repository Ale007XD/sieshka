-- migrations/001_initial_schema.sql — M1 PostgreSQL schema.
--
-- Run: psql $DATABASE_URL -f migrations/001_initial_schema.sql
-- Or via Alembic: alembic upgrade head
--
-- CONSTRAINTS:
--   - current_state stored in entity primary table (NO fsm_instances table — duplicates Trace)
--   - trace_id column on orders for M2/M3 YooKassa suspend/resume wiring
--   - All state writes go through terminal tools ONLY

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- ORDERS
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id     UUID NOT NULL,
    state           VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
    items           JSONB NOT NULL DEFAULT '[]',
    delivery_address TEXT NOT NULL,
    payment_id      VARCHAR(128),
    trace_id        VARCHAR(128),   -- wired to nano-vm trace in M3
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
CREATE INDEX IF NOT EXISTS idx_orders_trace_id ON orders(trace_id);

-- ============================================================
-- KITCHEN TICKETS
-- ============================================================
CREATE TABLE IF NOT EXISTS kitchen_tickets (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id    UUID NOT NULL REFERENCES orders(id),
    state       VARCHAR(32) NOT NULL DEFAULT 'NEW',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kitchen_tickets_order_id ON kitchen_tickets(order_id);

-- ============================================================
-- DELIVERY TASKS
-- ============================================================
CREATE TABLE IF NOT EXISTS delivery_tasks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    state           VARCHAR(32) NOT NULL DEFAULT 'UNASSIGNED',
    courier_id      UUID,
    picked_up_at    TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_delivery_tasks_order_id ON delivery_tasks(order_id);
CREATE INDEX IF NOT EXISTS idx_delivery_tasks_state ON delivery_tasks(state);

-- ============================================================
-- INVENTORY
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku         VARCHAR(128) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 0,
    state       VARCHAR(32) NOT NULL DEFAULT 'AVAILABLE',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- CUSTOMERS
-- ============================================================
CREATE TABLE IF NOT EXISTS customers (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone       VARCHAR(20) UNIQUE,
    name        VARCHAR(255),
    state       VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- PAYMENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS payments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    provider        VARCHAR(32) NOT NULL DEFAULT 'yookassa',
    provider_id     VARCHAR(128),
    amount          NUMERIC(12,2) NOT NULL,
    currency        VARCHAR(8) NOT NULL DEFAULT 'RUB',
    state           VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    raw_response    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_provider_id ON payments(provider_id);

-- ============================================================
-- IDEMPOTENCY KEYS (M2+) — mirrors nano-vm-mcp idempotency_keys
-- Prevents duplicate webhook processing before M3 integration
-- ============================================================
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         VARCHAR(256) PRIMARY KEY,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- updated_at trigger
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['orders','kitchen_tickets','delivery_tasks','payments']
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%I_updated_at ON %I;
             CREATE TRIGGER trg_%I_updated_at
             BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION update_updated_at();',
            t, t, t, t
        );
    END LOOP;
END;
$$;
