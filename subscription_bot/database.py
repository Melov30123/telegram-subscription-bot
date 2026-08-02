from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from subscription_bot.config import Settings
from subscription_bot.models import PaymentCompletion, PromoRedemption, Stats

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def load_migrations() -> list[tuple[str, str]]:
    migrations_dir = Path(__file__).with_name("migrations")
    return [
        (path.name, path.read_text(encoding="utf-8"))
        for path in sorted(migrations_dir.glob("*.sql"))
    ]


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.settings.database_url,
            min_size=self.settings.database_pool_min_size,
            max_size=self.settings.database_pool_max_size,
            command_timeout=30,
            max_inactive_connection_lifetime=300,
            server_settings={"application_name": "telegram_subscription_bot"},
        )
        await self.migrate()
        await self.ensure_default_plan()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Database is not connected")
        return self.pool

    async def health(self) -> bool:
        try:
            return await self._pool().fetchval("SELECT 1") == 1
        except (asyncpg.PostgresError, OSError):
            logger.exception("Database health check failed")
            return False

    async def migrate(self) -> None:
        migrations = load_migrations()
        async with self._pool().acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock(93741023)")
            try:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                applied = await conn.fetch("SELECT version FROM schema_migrations")
                applied_versions = {row["version"] for row in applied}
                for version, sql in migrations:
                    if version in applied_versions:
                        continue
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(
                            "INSERT INTO schema_migrations(version) VALUES($1)", version
                        )
                    logger.info("Applied database migration %s", version)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(93741023)")

    async def ensure_default_plan(self) -> None:
        await self._pool().execute(
            """
            INSERT INTO plans(code, title, price_stars, duration_days, sort_order)
            SELECT $1, $2, $3, $4, 10
            WHERE NOT EXISTS (SELECT 1 FROM plans WHERE is_active=TRUE)
            """,
            self.settings.default_plan_code.lower(),
            self.settings.default_plan_title,
            self.settings.default_plan_price_stars,
            self.settings.default_plan_duration_days,
        )

    async def upsert_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> None:
        language = (language_code or self.settings.default_language).split("-")[0].lower()
        if language not in {"ru", "en", "es"}:
            language = self.settings.default_language
        await self._pool().execute(
            """
            INSERT INTO users(telegram_id, username, first_name, last_name, language_code)
            VALUES($1, $2, $3, $4, $5)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_seen_at = NOW(),
                updated_at = NOW(),
                bot_blocked = FALSE
            """,
            telegram_id,
            username,
            first_name,
            last_name,
            language,
        )

    async def set_language(self, user_id: int, language: str) -> None:
        await self._pool().execute(
            "UPDATE users SET language_code=$2, updated_at=NOW() WHERE telegram_id=$1",
            user_id,
            language,
        )

    async def get_language(self, user_id: int) -> str:
        value = await self._pool().fetchval(
            "SELECT language_code FROM users WHERE telegram_id=$1", user_id
        )
        return value or self.settings.default_language

    async def get_user(self, user_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            """
            SELECT u.*,
                   (u.access_until IS NOT NULL AND u.access_until > NOW()) AS has_access,
                   (SELECT COUNT(*) FROM payments p WHERE p.user_id=u.telegram_id) AS payment_count,
                   (SELECT COALESCE(SUM(amount_stars), 0) FROM payments p
                    WHERE p.user_id=u.telegram_id AND p.status='paid') AS stars_paid
            FROM users u WHERE telegram_id=$1
            """,
            user_id,
        )

    async def list_users(
        self, query: str | None = None, *, limit: int = 10, offset: int = 0
    ) -> list[asyncpg.Record]:
        if query:
            numeric_id = int(query) if query.lstrip("-").isdigit() else None
            return await self._pool().fetch(
                """
                SELECT *, COUNT(*) OVER() AS full_count
                FROM users
                WHERE telegram_id=$1 OR username ILIKE $2 OR first_name ILIKE $2
                    OR last_name ILIKE $2
                ORDER BY created_at DESC LIMIT $3 OFFSET $4
                """,
                numeric_id,
                f"%{query.lstrip('@')}%",
                limit,
                offset,
            )
        return await self._pool().fetch(
            """
            SELECT *, COUNT(*) OVER() AS full_count
            FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

    async def export_users(self) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            """
            SELECT telegram_id, username, first_name, last_name, language_code,
                   access_until, is_blocked, bot_blocked, created_at, last_seen_at
            FROM users ORDER BY created_at
            """
        )

    async def mark_bot_blocked(self, user_id: int, blocked: bool = True) -> None:
        await self._pool().execute(
            "UPDATE users SET bot_blocked=$2, updated_at=NOW() WHERE telegram_id=$1",
            user_id,
            blocked,
        )

    async def set_user_blocked(self, admin_id: int, user_id: int, blocked: bool) -> bool:
        result = await self._pool().execute(
            "UPDATE users SET is_blocked=$2, updated_at=NOW() WHERE telegram_id=$1",
            user_id,
            blocked,
        )
        changed = result.endswith("1")
        if changed:
            await self.audit(
                admin_id,
                "user.block" if blocked else "user.unblock",
                user_id,
                {"blocked": blocked},
            )
        return changed

    async def get_active_plans(self) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            "SELECT * FROM plans WHERE is_active=TRUE ORDER BY sort_order, price_stars, id"
        )

    async def get_plans(self) -> list[asyncpg.Record]:
        return await self._pool().fetch("SELECT * FROM plans ORDER BY sort_order, id")

    async def get_plan(self, plan_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow("SELECT * FROM plans WHERE id=$1", plan_id)

    async def create_plan(
        self,
        admin_id: int,
        code: str,
        title: str,
        price_stars: int,
        duration_days: int,
    ) -> asyncpg.Record:
        row = await self._pool().fetchrow(
            """
            INSERT INTO plans(code, title, price_stars, duration_days)
            VALUES($1, $2, $3, $4) RETURNING *
            """,
            code.lower(),
            title,
            price_stars,
            duration_days,
        )
        await self.audit(admin_id, "plan.create", details={"code": code.lower()})
        assert row is not None
        return row

    async def toggle_plan(self, admin_id: int, code: str) -> asyncpg.Record | None:
        row = await self._pool().fetchrow(
            """
            UPDATE plans SET is_active=NOT is_active, updated_at=NOW()
            WHERE code=$1 RETURNING *
            """,
            code.lower(),
        )
        if row:
            await self.audit(
                admin_id, "plan.toggle", details={"code": code.lower(), "active": row["is_active"]}
            )
        return row

    async def create_payment_intent(self, user_id: int, plan_id: int) -> asyncpg.Record:
        intent_id = uuid.uuid4()
        row = await self._pool().fetchrow(
            """
            INSERT INTO payment_intents(id, user_id, plan_id, amount_stars, expires_at)
            SELECT $1, $2, id, price_stars, NOW() + INTERVAL '30 minutes'
            FROM plans WHERE id=$3 AND is_active=TRUE
            RETURNING *
            """,
            intent_id,
            user_id,
            plan_id,
        )
        if row is None:
            raise ValueError("Plan is unavailable")
        return row

    async def validate_payment_intent(
        self, intent_id: uuid.UUID, user_id: int, amount: int, currency: str
    ) -> bool:
        return bool(
            await self._pool().fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM payment_intents i
                    JOIN users u ON u.telegram_id=i.user_id
                    WHERE i.id=$1 AND i.user_id=$2 AND i.amount_stars=$3
                      AND $4='XTR' AND i.status='pending' AND i.expires_at>NOW()
                      AND NOT u.is_blocked
                )
                """,
                intent_id,
                user_id,
                amount,
                currency,
            )
        )

    async def complete_payment(
        self,
        *,
        intent_id: uuid.UUID,
        user_id: int,
        telegram_charge_id: str,
        provider_charge_id: str | None,
        amount: int,
        currency: str,
        raw_data: dict[str, Any],
    ) -> PaymentCompletion:
        async with self._pool().acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                """
                SELECT p.id, u.access_until FROM payments p
                JOIN users u ON u.telegram_id=p.user_id
                WHERE p.telegram_charge_id=$1
                """,
                telegram_charge_id,
            )
            if existing:
                return PaymentCompletion(existing["id"], existing["access_until"], True)

            intent = await conn.fetchrow(
                """
                SELECT i.*, p.duration_days FROM payment_intents i
                JOIN plans p ON p.id=i.plan_id
                WHERE i.id=$1 FOR UPDATE OF i
                """,
                intent_id,
            )
            if (
                intent is None
                or intent["user_id"] != user_id
                or intent["amount_stars"] != amount
                or currency != "XTR"
                or intent["status"] != "pending"
                or intent["expires_at"] <= utcnow()
            ):
                raise ValueError("Invalid or expired payment intent")

            user = await conn.fetchrow(
                "SELECT access_until FROM users WHERE telegram_id=$1 FOR UPDATE", user_id
            )
            if user is None:
                raise ValueError("Unknown payment user")
            starts_at = max(utcnow(), user["access_until"] or utcnow())
            ends_at = starts_at + timedelta(days=intent["duration_days"])
            payment_id = await conn.fetchval(
                """
                INSERT INTO payments(
                    telegram_charge_id, provider_charge_id, intent_id, user_id, plan_id,
                    amount_stars, currency, raw_data
                ) VALUES($1, $2, $3, $4, $5, $6, $7, $8::JSONB)
                RETURNING id
                """,
                telegram_charge_id,
                provider_charge_id,
                intent_id,
                user_id,
                intent["plan_id"],
                amount,
                currency,
                json.dumps(raw_data, ensure_ascii=False),
            )
            await conn.execute(
                """
                INSERT INTO subscriptions(
                    user_id, plan_id, payment_id, source, starts_at, ends_at, reason
                ) VALUES($1, $2, $3, 'payment', $4, $5, 'Telegram Stars')
                """,
                user_id,
                intent["plan_id"],
                payment_id,
                starts_at,
                ends_at,
            )
            await conn.execute(
                """
                UPDATE users SET access_until=$2, access_removed_at=NULL,
                    updated_at=NOW(), bot_blocked=FALSE WHERE telegram_id=$1
                """,
                user_id,
                ends_at,
            )
            await conn.execute(
                "UPDATE payment_intents SET status='paid', paid_at=NOW() WHERE id=$1", intent_id
            )
            return PaymentCompletion(payment_id, ends_at)

    async def grant_subscription(
        self, admin_id: int, user_id: int, days: int, reason: str = "Manual grant"
    ) -> datetime:
        if not 1 <= days <= 3650:
            raise ValueError("Days must be between 1 and 3650")
        async with self._pool().acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO users(telegram_id, language_code) VALUES($1, $2)
                ON CONFLICT(telegram_id) DO NOTHING
                """,
                user_id,
                self.settings.default_language,
            )
            user = await conn.fetchrow(
                "SELECT access_until FROM users WHERE telegram_id=$1 FOR UPDATE", user_id
            )
            starts_at = max(utcnow(), user["access_until"] or utcnow())
            ends_at = starts_at + timedelta(days=days)
            await conn.execute(
                """
                INSERT INTO subscriptions(user_id, source, granted_by, starts_at, ends_at, reason)
                VALUES($1, 'admin', $2, $3, $4, $5)
                """,
                user_id,
                admin_id,
                starts_at,
                ends_at,
                reason,
            )
            await conn.execute(
                """
                UPDATE users SET access_until=$2, access_removed_at=NULL, updated_at=NOW()
                WHERE telegram_id=$1
                """,
                user_id,
                ends_at,
            )
            await self._audit_on_connection(
                conn, admin_id, "subscription.grant", user_id, {"days": days, "reason": reason}
            )
            return ends_at

    async def revoke_subscription(self, admin_id: int, user_id: int, reason: str) -> bool:
        async with self._pool().acquire() as conn, conn.transaction():
            result = await conn.execute(
                """
                UPDATE users SET access_until=NOW(), access_removed_at=NULL, updated_at=NOW()
                WHERE telegram_id=$1
                """,
                user_id,
            )
            if not result.endswith("1"):
                return False
            await conn.execute(
                """
                UPDATE subscriptions SET status='revoked', reason=COALESCE(reason, '') || $2
                WHERE user_id=$1 AND status='active' AND ends_at>NOW()
                """,
                user_id,
                f" | revoked: {reason}",
            )
            await self._audit_on_connection(
                conn, admin_id, "subscription.revoke", user_id, {"reason": reason}
            )
            return True

    async def get_payment(self, charge_id: str) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            """
            SELECT p.*, pl.title AS plan_title FROM payments p
            JOIN plans pl ON pl.id=p.plan_id
            WHERE p.telegram_charge_id=$1
            """,
            charge_id,
        )

    async def list_payments(self, limit: int = 10) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            """
            SELECT p.*, pl.title AS plan_title, u.username
            FROM payments p JOIN plans pl ON pl.id=p.plan_id
            JOIN users u ON u.telegram_id=p.user_id
            ORDER BY p.created_at DESC LIMIT $1
            """,
            limit,
        )

    async def mark_payment_refunded(self, admin_id: int, charge_id: str) -> datetime | None:
        async with self._pool().acquire() as conn, conn.transaction():
            payment = await conn.fetchrow(
                "SELECT * FROM payments WHERE telegram_charge_id=$1 FOR UPDATE", charge_id
            )
            if payment is None or payment["status"] != "paid":
                return None
            subscription = await conn.fetchrow(
                "SELECT * FROM subscriptions WHERE payment_id=$1 FOR UPDATE", payment["id"]
            )
            user = await conn.fetchrow(
                "SELECT access_until FROM users WHERE telegram_id=$1 FOR UPDATE",
                payment["user_id"],
            )
            if subscription:
                duration = subscription["ends_at"] - subscription["starts_at"]
            else:
                duration_days = await conn.fetchval(
                    "SELECT duration_days FROM plans WHERE id=$1", payment["plan_id"]
                )
                duration = timedelta(days=duration_days or 0)
            new_until = max(utcnow(), (user["access_until"] or utcnow()) - duration)
            await conn.execute(
                "UPDATE payments SET status='refunded', refunded_at=NOW() WHERE id=$1",
                payment["id"],
            )
            if subscription:
                await conn.execute(
                    "UPDATE subscriptions SET status='refunded' WHERE payment_id=$1",
                    payment["id"],
                )
            await conn.execute(
                """
                UPDATE users SET access_until=$2, access_removed_at=NULL, updated_at=NOW()
                WHERE telegram_id=$1
                """,
                payment["user_id"],
                new_until,
            )
            await self._audit_on_connection(
                conn,
                admin_id,
                "payment.refund",
                payment["user_id"],
                {"charge_id": charge_id, "amount": payment["amount_stars"]},
            )
            return new_until

    async def create_invite(self, user_id: int, link: str, expires_at: datetime) -> None:
        await self._pool().execute(
            """
            INSERT INTO invite_links(user_id, invite_link, expires_at) VALUES($1, $2, $3)
            ON CONFLICT(invite_link) DO NOTHING
            """,
            user_id,
            link,
            expires_at,
        )

    async def active_invite_links(self, user_id: int) -> list[str]:
        rows = await self._pool().fetch(
            """
            SELECT invite_link FROM invite_links
            WHERE user_id=$1 AND revoked_at IS NULL AND expires_at>NOW()
            """,
            user_id,
        )
        return [row["invite_link"] for row in rows]

    async def mark_invite_revoked(self, link: str) -> None:
        await self._pool().execute(
            "UPDATE invite_links SET revoked_at=NOW() WHERE invite_link=$1", link
        )

    async def get_stats(self) -> Stats:
        row = await self._pool().fetchrow(
            """
            SELECT
                COUNT(*) AS users,
                COUNT(*) FILTER (WHERE access_until>NOW()) AS active,
                COUNT(*) FILTER (WHERE access_until IS NULL OR access_until<=NOW()) AS expired,
                COUNT(*) FILTER (WHERE bot_blocked) AS blocked_bot,
                COUNT(*) FILTER (WHERE created_at>=NOW()-INTERVAL '24 hours') AS new_users_24h
            FROM users
            """
        )
        revenue = await self._pool().fetchrow(
            """
            SELECT COUNT(*) FILTER (WHERE status='paid') AS payments,
                   COALESCE(SUM(amount_stars) FILTER (WHERE status='paid'), 0) AS stars_total,
                   COALESCE(SUM(amount_stars) FILTER (
                       WHERE status='paid' AND created_at>=NOW()-INTERVAL '30 days'
                   ), 0) AS stars_30d
            FROM payments
            """
        )
        return Stats(
            users=row["users"],
            active=row["active"],
            expired=row["expired"],
            blocked_bot=row["blocked_bot"],
            payments=revenue["payments"],
            stars_total=revenue["stars_total"],
            stars_30d=revenue["stars_30d"],
            new_users_24h=row["new_users_24h"],
        )

    async def expired_users_pending_removal(self, limit: int = 500) -> list[int]:
        rows = await self._pool().fetch(
            """
            SELECT telegram_id FROM users
            WHERE access_until IS NOT NULL AND access_until<=NOW()
              AND access_removed_at IS NULL
            ORDER BY access_until LIMIT $1
            """,
            limit,
        )
        return [row["telegram_id"] for row in rows]

    async def mark_access_removed(self, user_id: int) -> None:
        await self._pool().execute(
            "UPDATE users SET access_removed_at=NOW(), updated_at=NOW() WHERE telegram_id=$1",
            user_id,
        )

    async def due_reminders(self, days_before: int, limit: int = 1000) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            """
            SELECT u.telegram_id, u.language_code, u.access_until
            FROM users u
            WHERE u.access_until>NOW()+($1 * INTERVAL '1 day')
              AND u.access_until<=NOW()+(($1+1) * INTERVAL '1 day')
              AND NOT u.bot_blocked AND NOT u.is_blocked
              AND NOT EXISTS(
                  SELECT 1 FROM reminder_deliveries r
                  WHERE r.user_id=u.telegram_id AND r.access_until=u.access_until
                    AND r.days_before=$1
              )
            ORDER BY u.access_until LIMIT $2
            """,
            days_before,
            limit,
        )

    async def mark_reminder_delivered(
        self, user_id: int, access_until: datetime, days_before: int
    ) -> None:
        await self._pool().execute(
            """
            INSERT INTO reminder_deliveries(user_id, access_until, days_before)
            VALUES($1, $2, $3) ON CONFLICT DO NOTHING
            """,
            user_id,
            access_until,
            days_before,
        )

    async def create_promo(
        self, admin_id: int, code: str, days: int, max_uses: int | None
    ) -> asyncpg.Record:
        row = await self._pool().fetchrow(
            """
            INSERT INTO promo_codes(code, duration_days, max_uses, created_by)
            VALUES($1, $2, $3, $4) RETURNING *
            """,
            code.upper(),
            days,
            max_uses,
            admin_id,
        )
        await self.audit(admin_id, "promo.create", details={"code": code.upper()})
        assert row is not None
        return row

    async def list_promos(self, limit: int = 20) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            "SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT $1", limit
        )

    async def disable_promo(self, admin_id: int, code: str) -> bool:
        result = await self._pool().execute(
            "UPDATE promo_codes SET is_active=FALSE WHERE code=$1", code.upper()
        )
        changed = result.endswith("1")
        if changed:
            await self.audit(admin_id, "promo.disable", details={"code": code.upper()})
        return changed

    async def redeem_promo(self, user_id: int, code: str) -> PromoRedemption:
        async with self._pool().acquire() as conn, conn.transaction():
            promo = await conn.fetchrow(
                "SELECT * FROM promo_codes WHERE code=$1 FOR UPDATE", code.upper()
            )
            if promo is None or not promo["is_active"]:
                return PromoRedemption(False, "not_found")
            if promo["expires_at"] and promo["expires_at"] <= utcnow():
                return PromoRedemption(False, "expired")
            if promo["max_uses"] is not None and promo["used_count"] >= promo["max_uses"]:
                return PromoRedemption(False, "exhausted")
            used = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM promo_redemptions WHERE promo_id=$1 AND user_id=$2)",
                promo["id"],
                user_id,
            )
            if used:
                return PromoRedemption(False, "already_used")
            user = await conn.fetchrow(
                "SELECT access_until, is_blocked FROM users WHERE telegram_id=$1 FOR UPDATE",
                user_id,
            )
            if user is None:
                return PromoRedemption(False, "not_found")
            if user["is_blocked"]:
                return PromoRedemption(False, "blocked")
            starts_at = max(utcnow(), user["access_until"] or utcnow())
            ends_at = starts_at + timedelta(days=promo["duration_days"])
            subscription_id = await conn.fetchval(
                """
                INSERT INTO subscriptions(user_id, source, starts_at, ends_at, reason)
                VALUES($1, 'promo', $2, $3, $4) RETURNING id
                """,
                user_id,
                starts_at,
                ends_at,
                f"Promo {promo['code']}",
            )
            await conn.execute(
                """
                INSERT INTO promo_redemptions(promo_id, user_id, subscription_id)
                VALUES($1, $2, $3)
                """,
                promo["id"],
                user_id,
                subscription_id,
            )
            await conn.execute(
                "UPDATE promo_codes SET used_count=used_count+1 WHERE id=$1", promo["id"]
            )
            await conn.execute(
                """
                UPDATE users SET access_until=$2, access_removed_at=NULL, updated_at=NOW()
                WHERE telegram_id=$1
                """,
                user_id,
                ends_at,
            )
            return PromoRedemption(True, "ok", ends_at)

    async def create_broadcast(
        self, admin_id: int, source_chat_id: int, source_message_id: int, segment: str
    ) -> int:
        broadcast_id = await self._pool().fetchval(
            """
            INSERT INTO broadcasts(created_by, source_chat_id, source_message_id, segment)
            VALUES($1, $2, $3, $4) RETURNING id
            """,
            admin_id,
            source_chat_id,
            source_message_id,
            segment,
        )
        await self.audit(
            admin_id,
            "broadcast.create",
            details={"id": broadcast_id, "segment": segment},
        )
        return broadcast_id

    async def queue_broadcast(self, admin_id: int, broadcast_id: int) -> bool:
        result = await self._pool().execute(
            """
            UPDATE broadcasts SET status='queued'
            WHERE id=$1 AND created_by=$2 AND status='draft'
            """,
            broadcast_id,
            admin_id,
        )
        changed = result.endswith("1")
        if changed:
            await self.audit(admin_id, "broadcast.queue", details={"id": broadcast_id})
        return changed

    async def cancel_broadcast(self, admin_id: int, broadcast_id: int) -> bool:
        result = await self._pool().execute(
            """
            UPDATE broadcasts SET status='cancelled', finished_at=NOW()
            WHERE id=$1 AND created_by=$2 AND status IN ('draft', 'queued')
            """,
            broadcast_id,
            admin_id,
        )
        return result.endswith("1")

    async def claim_broadcast(self, broadcast_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            """
            UPDATE broadcasts SET status='running', started_at=NOW()
            WHERE id=$1 AND status='queued' RETURNING *
            """,
            broadcast_id,
        )

    async def queued_broadcast_ids(self) -> list[int]:
        rows = await self._pool().fetch(
            "SELECT id FROM broadcasts WHERE status='queued' ORDER BY created_at"
        )
        return [row["id"] for row in rows]

    async def broadcast_recipients(self, segment: str) -> list[int]:
        clauses = {
            "all": "TRUE",
            "active": "access_until>NOW()",
            "expired": "access_until IS NULL OR access_until<=NOW()",
        }
        condition = clauses.get(segment)
        if condition is None:
            raise ValueError("Unknown segment")
        query = (
            "SELECT telegram_id FROM users "
            f"WHERE NOT bot_blocked AND {condition} ORDER BY telegram_id"
        )
        rows = await self._pool().fetch(query)
        return [row["telegram_id"] for row in rows]

    async def update_broadcast_progress(
        self, broadcast_id: int, total: int, sent: int, failed: int
    ) -> None:
        await self._pool().execute(
            """
            UPDATE broadcasts SET total_count=$2, sent_count=$3, failed_count=$4 WHERE id=$1
            """,
            broadcast_id,
            total,
            sent,
            failed,
        )

    async def finish_broadcast(
        self, broadcast_id: int, sent: int, failed: int, error: str | None = None
    ) -> None:
        await self._pool().execute(
            """
            UPDATE broadcasts SET status=$2, sent_count=$3, failed_count=$4,
                finished_at=NOW(), error=$5 WHERE id=$1
            """,
            broadcast_id,
            "failed" if error else "completed",
            sent,
            failed,
            error,
        )

    async def cleanup(self) -> None:
        await self._pool().execute(
            """
            UPDATE payment_intents SET status='expired'
            WHERE status='pending' AND expires_at<=NOW();
            DELETE FROM reminder_deliveries WHERE delivered_at<NOW()-INTERVAL '1 year';
            """
        )

    async def audit(
        self,
        admin_id: int,
        action: str,
        target_user_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self._pool().acquire() as conn:
            await self._audit_on_connection(conn, admin_id, action, target_user_id, details)

    async def _audit_on_connection(
        self,
        conn: asyncpg.Connection,
        admin_id: int,
        action: str,
        target_user_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO admin_audit_log(admin_id, action, target_user_id, details)
            VALUES($1, $2, $3, $4::JSONB)
            """,
            admin_id,
            action,
            target_user_id,
            json.dumps(details or {}, ensure_ascii=False),
        )

    async def audit_log(self, limit: int = 20) -> Sequence[asyncpg.Record]:
        return await self._pool().fetch(
            "SELECT * FROM admin_audit_log ORDER BY created_at DESC LIMIT $1", limit
        )
