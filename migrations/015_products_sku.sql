-- migrations/015_products_sku.sql
--
-- products.sku — links a menu item to its inventory row (inventory.sku is
-- UNIQUE NOT NULL since migration 001, but nothing on the products side ever
-- referenced it: menu items and inventory rows were two disconnected tables,
-- inventory populated ad-hoc / by hand). Nullable here because existing
-- products have no sku assigned yet and backfill is a manual decision
-- (legacy inventory rows use hand-picked sku strings like 'BURGER-001' with
-- no guaranteed product name match) — not attempted by this migration.
--
-- sprint_inventory_menu_sync (2026-08). See DECISIONS.md.

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS sku VARCHAR(128) UNIQUE;

CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
