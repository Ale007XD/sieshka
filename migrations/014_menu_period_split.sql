-- migrations/014_menu_period_split.sql
--
-- BUG (found 2026-08-03): categories.menu_period was overloaded with two
-- incompatible vocabularies:
--   - app/services/menu_service.py (storefront query) treats it as TIME OF
--     DAY: 'morning' | 'evening' | 'both'.
--   - the admin category form / menu_agent_tools.py treats it as
--     FULFILLMENT METHOD: 'delivery' | 'pickup' | 'both'.
-- Setting a category to 'delivery' via admin silently removed it from the
-- storefront entirely (query only matches 'both'/'morning'/'evening').
--
-- Fix: split into two columns, each with its own CHECK constraint so the
-- collision cannot recur.

ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS time_period VARCHAR(16) NOT NULL DEFAULT 'both',
    ADD COLUMN IF NOT EXISTS fulfillment_scope VARCHAR(16) NOT NULL DEFAULT 'both';

-- Backfill from the old overloaded column. A value only makes sense in one
-- vocabulary or the other; the column it doesn't belong to gets the neutral
-- 'both' default (never narrows visibility beyond what's certain from the
-- old data).
UPDATE categories
SET time_period = CASE
        WHEN menu_period IN ('morning', 'evening', 'both') THEN menu_period
        ELSE 'both'
    END,
    fulfillment_scope = CASE
        WHEN menu_period IN ('delivery', 'pickup') THEN menu_period
        ELSE 'both'
    END;

ALTER TABLE categories
    ADD CONSTRAINT categories_time_period_check
        CHECK (time_period IN ('morning', 'evening', 'both')),
    ADD CONSTRAINT categories_fulfillment_scope_check
        CHECK (fulfillment_scope IN ('delivery', 'pickup', 'both'));

ALTER TABLE categories DROP COLUMN IF EXISTS menu_period;

-- products.menu_period_override was always time-of-day only (menu_service.py
-- reads it as an override of the category's time_period; fulfillment_scope
-- has never had a per-product override in this schema or in the admin UI).
-- Renamed for consistency with the split above, semantics unchanged.
ALTER TABLE products RENAME COLUMN menu_period_override TO time_period_override;

ALTER TABLE products
    ADD CONSTRAINT products_time_period_override_check
        CHECK (time_period_override IS NULL OR time_period_override IN ('morning', 'evening', 'both'));