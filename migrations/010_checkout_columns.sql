-- migrations/010_checkout_columns.sql — sprint_m7_checkout_wiring order columns.
--
-- Adds the real cart.js checkout fields to orders. `items` is already JSONB
-- and now stores typed, price-snapshotted OrderItem rows (see
-- app/domains/orders/models.py). New columns are nullable so existing rows
-- from earlier milestones remain valid.

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(32),
    ADD COLUMN IF NOT EXISTS zone_id INTEGER,
    ADD COLUMN IF NOT EXISTS comment TEXT,
    ADD COLUMN IF NOT EXISTS client_max_uid INTEGER,
    ADD COLUMN IF NOT EXISTS total_rub INTEGER,
    ADD COLUMN IF NOT EXISTS payment_method VARCHAR(32);
