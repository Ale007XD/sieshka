-- migrations/006_delivery_zones.sql — M7 delivery zones reference data.
--
-- DeliveryZone is reference data: it describes delivery-time estimates and
-- availability per zone. It carries NO price field (delivery pricing is a single
-- FLAT fee in app/config.py, exposed via GET /api/config/delivery-fee). Zones
-- affect ETA and availability (is_active) only.
--
-- SCOPE BOUNDARY: this is plain reference data, loaded once via
-- scripts/import_delivery_zones.py from the real 3-zone export. It is not a
-- stateful entity and has no FSM.

CREATE TABLE IF NOT EXISTS delivery_zones (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id           VARCHAR(255),
    name                  VARCHAR(255) NOT NULL,
    delivery_time_minutes INTEGER NOT NULL,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_delivery_zones_active ON delivery_zones(is_active);
