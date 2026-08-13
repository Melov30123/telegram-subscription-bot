CREATE TABLE guide_payment_intents (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    amount_stars INTEGER NOT NULL CHECK (amount_stars > 0),
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'expired', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    paid_at TIMESTAMPTZ
);

CREATE INDEX guide_payment_intents_pending_idx ON guide_payment_intents (expires_at)
    WHERE status = 'pending';

CREATE TABLE guide_purchases (
    id BIGSERIAL PRIMARY KEY,
    telegram_charge_id TEXT NOT NULL UNIQUE,
    provider_charge_id TEXT,
    intent_id UUID NOT NULL UNIQUE REFERENCES guide_payment_intents(id),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id),
    amount_stars INTEGER NOT NULL CHECK (amount_stars > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'XTR' CHECK (currency = 'XTR'),
    status VARCHAR(16) NOT NULL DEFAULT 'paid'
        CHECK (status IN ('paid', 'refunded', 'chargeback')),
    raw_data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    refunded_at TIMESTAMPTZ
);

CREATE INDEX guide_purchases_user_created_idx
    ON guide_purchases (user_id, created_at DESC);
CREATE INDEX guide_purchases_created_idx ON guide_purchases (created_at DESC);
