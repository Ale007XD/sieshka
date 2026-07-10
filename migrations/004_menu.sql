-- migrations/004_menu.sql — M7 categories + products tables.
-- Self-referential parent_category_id resolved by name at seed time.

CREATE TABLE IF NOT EXISTS categories (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id         VARCHAR(64),
    name                VARCHAR(255) NOT NULL,
    parent_category_id  UUID REFERENCES categories(id),
    menu_period         VARCHAR(16) NOT NULL DEFAULT 'both',
    sort                INTEGER NOT NULL DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_category_id);
CREATE INDEX IF NOT EXISTS idx_categories_name ON categories(name);
CREATE INDEX IF NOT EXISTS idx_categories_external_id ON categories(external_id);

CREATE TABLE IF NOT EXISTS products (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                  VARCHAR(255) NOT NULL,
    category_id           UUID REFERENCES categories(id),
    menu_period_override  VARCHAR(16),
    price_rub             INTEGER,
    description           TEXT,
    image_url             TEXT,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_products_updated_at'
    ) THEN
        CREATE TRIGGER trg_products_updated_at
        BEFORE UPDATE ON products
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    END IF;
END;
$$;
