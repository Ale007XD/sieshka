-- migrations/003_promotions.sql — Promotions table for dashboard panel.
-- Run separately: psql $DATABASE_URL -f migrations/003_promotions.sql

CREATE TABLE IF NOT EXISTS promotions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    discount    NUMERIC(5,2) NOT NULL,
    state       VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
