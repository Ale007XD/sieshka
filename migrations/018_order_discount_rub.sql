ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS discount_rub INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN orders.discount_rub IS
    'Promo-code discount actually applied at checkout time, in RUB. Persisted
     (not recomputed from promotions.discount at read time) — same rationale
     as OrderItem price/name snapshotting: a promotion edited or deactivated
     after the order was placed must not silently change what an
     ALREADY-PLACED order''s thanks-page/receipt shows it saved. Always 0
     when no promo_code was applied, never NULL.';
