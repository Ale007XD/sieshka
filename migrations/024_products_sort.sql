-- migrations/024_products_sort.sql — sprint_menu_product_reorder.
--
-- Adds products.sort — same role as categories.sort (004_menu.sql): explicit
-- display order, read by the storefront query (menu_service.py::_fetch_products)
-- and written by the admin reorder arrows (menu_admin.html, mirrors the
-- existing category reorder UI). Order is scoped per category on the
-- front-end (products are grouped by category there); the column itself
-- carries no category-scoping constraint — reorder is done by renormalizing
-- one category's rows at a time (same convention as categories.sort).
--
-- Existing rows default to 0 (all tied — falls back to the current
-- "ORDER BY name" tiebreak for any product that hasn't been reordered yet).

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS sort INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_products_category_sort ON products(category_id, sort);
