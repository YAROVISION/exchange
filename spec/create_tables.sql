-- 1. Створення таблиці гаманців (wallets)
CREATE TABLE IF NOT EXISTS public.wallets (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    usd_balance NUMERIC(20, 8) NOT NULL DEFAULT 100.00000000 CHECK (usd_balance >= 0),
    btc_balance NUMERIC(20, 8) NOT NULL DEFAULT 0.00000000 CHECK (btc_balance >= 0),
    avg_buy_price NUMERIC(20, 8) NOT NULL DEFAULT 0.00000000 CHECK (avg_buy_price >= 0),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Створення таблиці угод (trades)
CREATE TABLE IF NOT EXISTS public.trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type VARCHAR(10) NOT NULL CHECK (type IN ('buy', 'sell')),
    btc_amount NUMERIC(20, 8) NOT NULL CHECK (btc_amount > 0),
    price NUMERIC(20, 8) NOT NULL CHECK (price > 0),
    fee NUMERIC(20, 8) NOT NULL CHECK (fee >= 0),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Створення індексу для швидкого пошуку історії угод користувача
CREATE INDEX IF NOT EXISTS idx_trades_user_id ON public.trades(user_id);

-- 3. Створення функції тригера для автоматичного створення гаманця при реєстрації
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.wallets (user_id, usd_balance, btc_balance, avg_buy_price)
    VALUES (NEW.id, 100.00000000, 0.00000000, 0.00000000);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 4. Прив'язка тригера до створення користувача в Supabase Auth
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
