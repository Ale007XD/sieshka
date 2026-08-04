-- migrations/016_inventory_movements.sql
--
-- Delta-log ledger for inventory quantity changes. Until now `inventory`
-- only held current quantity — no history of restocks/sales/adjustments,
-- so export-by-period and stats visualization had no data source.
--
-- Granularity: one row per mutation (delta-log), not aggregated by day/SKU.
-- Sufficient for a single-kitchen local deployment (sprint_inventory_ledger,
-- 2026-08 decision). batch_id is nullable and unused for now — an explicit
-- extension point if per-unit/batch tracking is ever needed on top of this
-- delta-log; adding it now avoids a migration on existing rows later.
--
-- below_zero: written by the caller (decrement path) when the resulting
-- quantity goes negative. sprint_inventory_ledger only adds the column;
-- sprint_inventory_sale_decrement is what actually allows negative qty
-- through the order flow and sets this flag — see DECISIONS.md.

CREATE TABLE IF NOT EXISTS inventory_movements (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku          VARCHAR(128) NOT NULL,
    delta        INTEGER NOT NULL,
    reason       VARCHAR(32) NOT NULL,
    source_type  VARCHAR(32),
    source_id    VARCHAR(128),
    below_zero   BOOLEAN NOT NULL DEFAULT FALSE,
    batch_id     UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT inventory_movements_reason_check CHECK (
        reason IN ('RESTOCK_MANUAL', 'RESTOCK_AGENT', 'SALE', 'ADJUSTMENT', 'SYNC_INIT')
    )
);

CREATE INDEX IF NOT EXISTS idx_inventory_movements_sku ON inventory_movements(sku);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_created_at ON inventory_movements(created_at);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_sku_created_at
    ON inventory_movements(sku, created_at);
