-- migrations/022_orders_client_zalo_uid.sql — sprint_zalo_storefront_auth.
--
-- Mirrors client_max_uid (010_checkout_columns.sql): a server-verified
-- attribution field, NOT a customer identity/session mechanism. Customer
-- identity in this project is, and remains, phone number
-- (CustomerService.find_or_create_by_phone) — this column only records
-- which Zalo Mini App user placed the order, for notification-targeting/
-- analytics, the same role client_max_uid already plays for MAX.
--
-- VARCHAR, not INTEGER like client_max_uid: Zalo user IDs are opaque
-- per-app strings (same reasoning as staff.zalo_user_id,
-- 021_staff_zalo_user_id.sql), not guaranteed-numeric like MAX's.

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS client_zalo_uid VARCHAR(64);
