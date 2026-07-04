-- migrations/002_narrative_receipt.sql — M4 NarrativeReceipt table.
--
-- ADR-002: NarrativeReceipt stored in PostgreSQL (NOT SQLite).
-- Fields: decision, reason, rules[], trace_ids[] only.
-- trace_ids[] references ExecutionReceipt.trace_id — read-only reference.

CREATE TABLE IF NOT EXISTS narrative_receipts (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision    VARCHAR(64) NOT NULL,
    reason      TEXT NOT NULL,
    rules       JSONB NOT NULL DEFAULT '[]',
    trace_ids   JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_narrative_receipts_decision ON narrative_receipts(decision);
CREATE INDEX IF NOT EXISTS idx_narrative_receipts_trace_ids ON narrative_receipts USING GIN (trace_ids);
