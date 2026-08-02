-- Preserve and import the schema used by the original single-file v3 bot.
DO $$
BEGIN
    IF TO_REGCLASS('public.users') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='users' AND column_name='telegram_id'
       ) THEN
        ALTER TABLE users RENAME TO users_legacy_v3;
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='users_pkey') THEN
            ALTER TABLE users_legacy_v3 RENAME CONSTRAINT users_pkey TO users_legacy_v3_pkey;
        END IF;
    END IF;

    IF TO_REGCLASS('public.payments') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema='public' AND table_name='payments'
             AND column_name='telegram_charge_id'
       ) THEN
        ALTER TABLE payments RENAME TO payments_legacy_v3;
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='payments_pkey') THEN
            ALTER TABLE payments_legacy_v3 RENAME CONSTRAINT payments_pkey
                TO payments_legacy_v3_pkey;
        END IF;
    END IF;
END $$;

CREATE TABLE users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code VARCHAR(8) NOT NULL DEFAULT 'ru',
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    bot_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    access_until TIMESTAMPTZ,
    access_removed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX users_access_until_idx ON users (access_until)
    WHERE access_until IS NOT NULL;
CREATE INDEX users_last_seen_idx ON users (last_seen_at DESC);
CREATE INDEX users_username_lower_idx ON users (LOWER(username));

CREATE TABLE plans (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(48) NOT NULL UNIQUE,
    title VARCHAR(128) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_stars INTEGER NOT NULL CHECK (price_stars BETWEEN 1 AND 10000),
    duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 3650),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (code = LOWER(code))
);

CREATE INDEX plans_active_order_idx ON plans (is_active, sort_order, id);

CREATE TABLE payment_intents (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    plan_id BIGINT NOT NULL REFERENCES plans(id),
    amount_stars INTEGER NOT NULL CHECK (amount_stars > 0),
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'expired', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    paid_at TIMESTAMPTZ
);

CREATE INDEX payment_intents_pending_idx ON payment_intents (expires_at)
    WHERE status = 'pending';

CREATE TABLE payments (
    id BIGSERIAL PRIMARY KEY,
    telegram_charge_id TEXT NOT NULL UNIQUE,
    provider_charge_id TEXT,
    intent_id UUID NOT NULL UNIQUE REFERENCES payment_intents(id),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id),
    plan_id BIGINT NOT NULL REFERENCES plans(id),
    amount_stars INTEGER NOT NULL CHECK (amount_stars > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'XTR' CHECK (currency = 'XTR'),
    status VARCHAR(16) NOT NULL DEFAULT 'paid'
        CHECK (status IN ('paid', 'refunded', 'chargeback')),
    raw_data JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    refunded_at TIMESTAMPTZ
);

CREATE INDEX payments_user_created_idx ON payments (user_id, created_at DESC);
CREATE INDEX payments_created_idx ON payments (created_at DESC);

CREATE TABLE subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    plan_id BIGINT REFERENCES plans(id),
    payment_id BIGINT UNIQUE REFERENCES payments(id),
    source VARCHAR(16) NOT NULL CHECK (source IN ('payment', 'admin', 'promo')),
    granted_by BIGINT,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked', 'refunded')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);

CREATE INDEX subscriptions_user_ends_idx ON subscriptions (user_id, ends_at DESC);
CREATE INDEX subscriptions_active_ends_idx ON subscriptions (ends_at)
    WHERE status = 'active';

CREATE TABLE invite_links (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    invite_link TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE INDEX invite_links_user_active_idx ON invite_links (user_id, created_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE promo_codes (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    duration_days INTEGER NOT NULL CHECK (duration_days BETWEEN 1 AND 3650),
    max_uses INTEGER CHECK (max_uses IS NULL OR max_uses > 0),
    used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (code = UPPER(code))
);

CREATE TABLE promo_redemptions (
    promo_id BIGINT NOT NULL REFERENCES promo_codes(id),
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    subscription_id BIGINT NOT NULL UNIQUE REFERENCES subscriptions(id),
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (promo_id, user_id)
);

CREATE TABLE reminder_deliveries (
    user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    access_until TIMESTAMPTZ NOT NULL,
    days_before INTEGER NOT NULL,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, access_until, days_before)
);

CREATE TABLE broadcasts (
    id BIGSERIAL PRIMARY KEY,
    created_by BIGINT NOT NULL,
    source_chat_id BIGINT NOT NULL,
    source_message_id BIGINT NOT NULL,
    segment VARCHAR(16) NOT NULL CHECK (segment IN ('all', 'active', 'expired')),
    status VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'queued', 'running', 'completed', 'cancelled', 'failed')),
    total_count INTEGER NOT NULL DEFAULT 0,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT
);

CREATE INDEX broadcasts_status_idx ON broadcasts (status, created_at);

CREATE TABLE admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    action VARCHAR(64) NOT NULL,
    target_user_id BIGINT,
    details JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX admin_audit_created_idx ON admin_audit_log (created_at DESC);
CREATE INDEX admin_audit_admin_idx ON admin_audit_log (admin_id, created_at DESC);

-- Import v3 data without deleting the original renamed tables. The previous schema did
-- not retain enough information to rebuild an exact subscription period per payment, so
-- current access is imported as a separate legacy grant while payments keep their history.
DO $$
DECLARE
    legacy_plan_id BIGINT;
BEGIN
    IF TO_REGCLASS('public.users_legacy_v3') IS NOT NULL THEN
        EXECUTE $sql$
            INSERT INTO users(telegram_id, access_until, created_at, updated_at, last_seen_at)
            SELECT user_id,
                   CASE WHEN is_active THEN subscription_end ELSE NULL END,
                   COALESCE(created_at, NOW()), NOW(), COALESCE(created_at, NOW())
            FROM users_legacy_v3
            ON CONFLICT(telegram_id) DO NOTHING
        $sql$;

        EXECUTE $sql$
            INSERT INTO subscriptions(user_id, source, starts_at, ends_at, reason)
            SELECT user_id, 'admin',
                   LEAST(COALESCE(created_at, NOW()), subscription_end - INTERVAL '1 second'),
                   subscription_end, 'Imported from v3'
            FROM users_legacy_v3
            WHERE is_active AND subscription_end IS NOT NULL
        $sql$;
    END IF;

    IF TO_REGCLASS('public.payments_legacy_v3') IS NOT NULL THEN
        EXECUTE $sql$
            INSERT INTO users(telegram_id, created_at, updated_at, last_seen_at)
            SELECT user_id, COALESCE(MIN(created_at), NOW()), NOW(),
                   COALESCE(MIN(created_at), NOW())
            FROM payments_legacy_v3
            GROUP BY user_id
            ON CONFLICT(telegram_id) DO NOTHING
        $sql$;

        INSERT INTO plans(code, title, description, price_stars, duration_days, is_active, sort_order)
        VALUES('legacy', 'Legacy v3', 'Imported payment records', 1, 30, FALSE, 9999)
        ON CONFLICT(code) DO UPDATE SET title=EXCLUDED.title
        RETURNING id INTO legacy_plan_id;

        IF legacy_plan_id IS NULL THEN
            SELECT id INTO legacy_plan_id FROM plans WHERE code='legacy';
        END IF;

        EXECUTE format($sql$
            INSERT INTO payment_intents(
                id, user_id, plan_id, amount_stars, status, created_at, expires_at, paid_at
            )
            SELECT MD5('legacy:' || payment_id)::UUID, user_id, %s, amount, 'paid',
                   COALESCE(created_at, NOW()), COALESCE(created_at, NOW()),
                   COALESCE(created_at, NOW())
            FROM payments_legacy_v3
            WHERE EXISTS (SELECT 1 FROM users u WHERE u.telegram_id=user_id)
            ON CONFLICT(id) DO NOTHING
        $sql$, legacy_plan_id);

        EXECUTE format($sql$
            INSERT INTO payments(
                telegram_charge_id, intent_id, user_id, plan_id, amount_stars,
                currency, status, raw_data, created_at
            )
            SELECT payment_id, MD5('legacy:' || payment_id)::UUID, user_id, %s, amount,
                   'XTR', 'paid', JSONB_BUILD_OBJECT('legacy_payload', payload),
                   COALESCE(created_at, NOW())
            FROM payments_legacy_v3
            WHERE EXISTS (SELECT 1 FROM users u WHERE u.telegram_id=user_id)
            ON CONFLICT(telegram_charge_id) DO NOTHING
        $sql$, legacy_plan_id);
    END IF;
END $$;
