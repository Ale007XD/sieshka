-- migrations/012_promotions_effect_type.sql
ALTER TABLE promotions
    ADD COLUMN IF NOT EXISTS effect_type VARCHAR(32) NOT NULL DEFAULT 'PERCENT_DISCOUNT',
    ADD COLUMN IF NOT EXISTS trigger_code VARCHAR(64);

ALTER TABLE promotions
    ADD CONSTRAINT chk_promotions_effect_type
    CHECK (effect_type IN ('PERCENT_DISCOUNT', 'FIXED_AMOUNT', 'FREE_DELIVERY'));

-- discount уже NUMERIC(5,2) — этого достаточно для % (0-100), но тесно для
-- фиксированной суммы в рублях (макс 999.99). Расширяем тип, не меняя имя
-- колонки — она и так уже означает "величина эффекта", просто раньше это
-- было только процентом.
ALTER TABLE promotions
    ALTER COLUMN discount TYPE NUMERIC(10, 2);

COMMENT ON COLUMN promotions.discount IS
    'Effect value: percent (0-100) for PERCENT_DISCOUNT, rubles for FIXED_AMOUNT, NULL/unused for FREE_DELIVERY';

CREATE UNIQUE INDEX IF NOT EXISTS idx_promotions_trigger_code
    ON promotions (lower(trigger_code)) WHERE trigger_code IS NOT NULL;

-- promo_code — отдельная колонка заказа, НЕ orders.comment. Разводит
-- промокод и инструкции курьеру на уровне данных, не только UI.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS promo_code VARCHAR(64);