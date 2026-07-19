-- migrations/011_zone_id_uuid.sql — fix orders.zone_id type mismatch.
--
-- orders.zone_id was INTEGER (migrations/010_checkout_columns.sql), but
-- delivery_zones.id is UUID (migrations/006_delivery_zones.sql). It only
-- ever "worked" because cart.js's parseInt() coincidentally matched the 3
-- originally-seeded zones' numeric external_id, and because there was no FK
-- constraint at all to catch a mismatched/garbage value. Any zone created
-- via sprint_m7_zone_agent's apply_zone_command has external_id=NULL,
-- silently reproducing the bug. No production orders exist yet at this
-- stage (pre-launch dev data only) — a drop+recreate is safe; a real
-- launched system would need a data-preserving migration instead.

ALTER TABLE orders DROP COLUMN IF EXISTS zone_id;
ALTER TABLE orders
    ADD COLUMN zone_id UUID REFERENCES delivery_zones(id);
