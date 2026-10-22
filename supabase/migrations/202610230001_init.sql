-- Apply in the selected Supabase project after reviewing the target database.
CREATE TABLE IF NOT EXISTS public.products (
    id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.price_groups (
    id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    mode TEXT NOT NULL CHECK (mode IN ('direct', 'multiplier')),
    factor NUMERIC(14,6) NOT NULL DEFAULT 1.0 CHECK (factor > 0),
    fixed_delta BIGINT NOT NULL DEFAULT 0, active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE (side, name)
);
CREATE TABLE IF NOT EXISTS public.app_users (
    telegram_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT NOT NULL DEFAULT '',
    account_type TEXT NOT NULL DEFAULT 'regular' CHECK (account_type IN ('regular', 'special')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    can_buy BOOLEAN NOT NULL DEFAULT FALSE, can_sell BOOLEAN NOT NULL DEFAULT FALSE,
    buy_group_id BIGINT REFERENCES public.price_groups(id) ON DELETE SET NULL,
    sell_group_id BIGINT REFERENCES public.price_groups(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.base_prices (
    product_id BIGINT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    amount BIGINT NOT NULL CHECK (amount >= 0), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (product_id, side)
);
CREATE TABLE IF NOT EXISTS public.group_prices (
    product_id BIGINT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    group_id BIGINT NOT NULL REFERENCES public.price_groups(id) ON DELETE CASCADE,
    direct_price BIGINT CHECK (direct_price IS NULL OR direct_price >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (product_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_users_status ON public.app_users(status);
CREATE INDEX IF NOT EXISTS idx_group_prices_group ON public.group_prices(group_id);
CREATE INDEX IF NOT EXISTS idx_base_prices_side ON public.base_prices(side);
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_users_updated_at ON public.app_users;
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON public.app_users
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS trg_base_prices_updated_at ON public.base_prices;
CREATE TRIGGER trg_base_prices_updated_at BEFORE UPDATE ON public.base_prices
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS trg_group_prices_updated_at ON public.group_prices;
CREATE TRIGGER trg_group_prices_updated_at BEFORE UPDATE ON public.group_prices
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- The bot connects server-side using a protected PostgreSQL connection string.
-- Do not expose direct table access through public API roles.
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.base_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.group_prices ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.products, public.price_groups, public.app_users, public.base_prices, public.group_prices FROM anon, authenticated;
