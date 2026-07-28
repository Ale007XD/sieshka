ALTER TABLE delivery_zones
    ADD COLUMN IF NOT EXISTS delivery_fee_rub INTEGER NOT NULL DEFAULT 99;

COMMENT ON COLUMN delivery_zones.delivery_fee_rub IS
    'Per-zone flat delivery fee in RUB. Replaces the old global settings.DELIVERY_FEE
     as the authoritative source for delivery pricing — settings.DELIVERY_FEE remains
     only as a fallback for pickup orders (zone_id is None) or orphaned zone_id values.';