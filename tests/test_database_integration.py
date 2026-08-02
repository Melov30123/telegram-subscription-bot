from __future__ import annotations

# ruff: noqa: E402, I001

import asyncio
import os
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")

from subscription_bot.config import Settings  # noqa: E402
from subscription_bot.database import Database  # noqa: E402

ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL")
FRESH_DATABASE = "subscription_bot_fresh_test"
LEGACY_DATABASE = "subscription_bot_legacy_test"


def settings_for(database_name: str) -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:test",
        database_url=f"postgresql://postgres:postgres@127.0.0.1:5432/{database_name}",
        channel_id=-1001234567890,
        admin_ids=[1],
        default_plan_code="monthly",
        default_plan_title="Monthly",
        default_plan_price_stars=100,
        default_plan_duration_days=30,
        database_pool_min_size=1,
        database_pool_max_size=3,
    )


async def recreate_database(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def drop_database(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def fresh_database_scenario() -> None:
    await recreate_database(FRESH_DATABASE)
    database = Database(settings_for(FRESH_DATABASE))
    try:
        await database.connect()
        await database.upsert_user(42, "buyer", "Test", "User", "en")
        plans = await database.get_active_plans()
        assert len(plans) == 1

        intent = await database.create_payment_intent(42, plans[0]["id"])
        assert await database.validate_payment_intent(intent["id"], 42, 100, "XTR")
        result = await database.complete_payment(
            intent_id=uuid.UUID(str(intent["id"])),
            user_id=42,
            telegram_charge_id="test-charge-1",
            provider_charge_id="",
            amount=100,
            currency="XTR",
            raw_data={"test": True},
        )
        duplicate = await database.complete_payment(
            intent_id=uuid.UUID(str(intent["id"])),
            user_id=42,
            telegram_charge_id="test-charge-1",
            provider_charge_id="",
            amount=100,
            currency="XTR",
            raw_data={"test": True},
        )
        assert duplicate.already_processed is True
        assert duplicate.payment_id == result.payment_id
        assert (await database.get_user(42))["has_access"] is True
        stats = await database.get_stats()
        assert stats.users == 1
        assert stats.active == 1
        assert stats.payments == 1
        assert stats.stars_total == 100
    finally:
        await database.close()
        await drop_database(FRESH_DATABASE)


async def legacy_database_scenario() -> None:
    await recreate_database(LEGACY_DATABASE)
    settings = settings_for(LEGACY_DATABASE)
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            """
            CREATE TABLE users(
                user_id BIGINT PRIMARY KEY,
                subscription_end TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE payments(
                payment_id TEXT PRIMARY KEY,
                user_id BIGINT,
                amount INTEGER,
                payload TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            INSERT INTO users(user_id, subscription_end, is_active)
            VALUES(99, NOW() + INTERVAL '14 days', TRUE);
            INSERT INTO payments(payment_id, user_id, amount, payload)
            VALUES('legacy-charge', 99, 15, 'subscription_99');
            """
        )
    finally:
        await conn.close()

    database = Database(settings)
    try:
        await database.connect()
        user = await database.get_user(99)
        payment = await database.get_payment("legacy-charge")
        assert user is not None and user["has_access"] is True
        assert payment is not None and payment["amount_stars"] == 15
        assert await database._pool().fetchval("SELECT TO_REGCLASS('users_legacy_v3')")
        assert await database._pool().fetchval("SELECT TO_REGCLASS('payments_legacy_v3')")
        assert (await database.get_active_plans())[0]["code"] == "monthly"
    finally:
        await database.close()
        await drop_database(LEGACY_DATABASE)


@pytest.mark.skipif(not ADMIN_URL, reason="TEST_POSTGRES_ADMIN_URL is not configured")
def test_postgresql_migrations_and_payment_transaction() -> None:
    asyncio.run(fresh_database_scenario())


@pytest.mark.skipif(not ADMIN_URL, reason="TEST_POSTGRES_ADMIN_URL is not configured")
def test_legacy_v3_schema_is_imported() -> None:
    asyncio.run(legacy_database_scenario())
