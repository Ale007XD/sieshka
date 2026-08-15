-- migrations/023_orders_client_telegram_uid.sql — sprint_telegram_miniapp_auth.
--
-- Mirrors client_max_uid (010_checkout_columns.sql): a server-verified
-- attribution field, NOT a customer identity/session mechanism. Customer
-- identity in this project is, and remains, phone number
-- (CustomerService.find_or_create_by_phone) — this column only records
-- which Telegram Mini App user placed the order, for notification-
-- targeting/analytics, the same role client_max_uid already plays for MAX.
--
-- INTEGER, same as client_max_uid (not VARCHAR like client_zalo_uid):
-- Telegram user ids are guaranteed-numeric per Telegram's own Mini Apps
-- spec, and app/services/max_webapp_auth.py::validate_init_data() already
-- returns int for this exact reason (its algorithm is byte-identical to
-- Telegram's documented initData validation — see that module's docstring).

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS client_telegram_uid INTEGER;
