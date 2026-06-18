# =========================================================
#                 TELEGRAM SUBSCRIPTION BOT V3
# =========================================================

import asyncio
import logging
import os
import threading
import http.server
import socketserver
import time
import json

from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from aiogram import (
    Bot,
    Dispatcher,
    F,
    types
)

from aiogram.filters import (
    Command,
    CommandObject
)

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery
)

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter
)

from apscheduler.schedulers.asyncio import (
    AsyncIOScheduler
)

from apscheduler.triggers.cron import (
    CronTrigger
)

from dotenv import load_dotenv


# =========================================================
#                       ENV
# =========================================================

load_dotenv()


def get_env(name: str) -> str:

    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"ENV variable not found: {name}"
        )

    return value


BOT_TOKEN = get_env(
    "BOT_TOKEN"
)

DATABASE_URL = get_env(
    "DATABASE_URL"
)

CHANNEL_ID = get_env(
    "CHANNEL_ID"
)

ADMIN_ID = int(
    get_env(
        "ADMIN_ID"
    )
)

PRICE_STARS = int(
    os.getenv(
        "PRICE_STARS",
        "15"
    )
)

SUBSCRIPTION_DAYS = int(
    os.getenv(
        "SUBSCRIPTION_DAYS",
        "30"
    )
)


# =========================================================
#                     LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "subscription_bot"
)


# =========================================================
#                  GLOBAL OBJECTS
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()

scheduler = AsyncIOScheduler()

db_pool: Optional[
    asyncpg.Pool
] = None


# =========================================================
#                   MONITORING
# =========================================================

START_TIME = datetime.now(
    timezone.utc
)

LAST_PING = None

BUY_COOLDOWN = {}

BUY_TIMEOUT = 5


# =========================================================
#                  HELPER FUNCTIONS
# =========================================================

def now():

    return datetime.now(
        timezone.utc
    )


def format_date(
    value: datetime
):

    if not value:
        return "-"

    return value.strftime(
        "%d.%m.%Y %H:%M"
    )


async def admin_only(
    message: types.Message
):

    if (
        message.from_user.id
        != ADMIN_ID
    ):

        await message.answer(
            "⛔ Эта команда доступна только администратору."
        )

        return False

    return True


# =========================================================
#                   DATABASE
# =========================================================

async def init_database():

    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10
    )

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id BIGINT PRIMARY KEY,
                subscription_end TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments(
                payment_id TEXT PRIMARY KEY,
                user_id BIGINT,
                amount INTEGER,
                payload TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )

    logger.info(
        "Database ready"
    )


# =========================================================
#                    USERS
# =========================================================

async def get_user(
    user_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT *
            FROM users
            WHERE user_id=$1
            """,
            user_id
        )


async def set_subscription(
    user_id: int,
    end_date: datetime
):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO users(
                user_id,
                subscription_end,
                is_active
            )
            VALUES(
                $1,
                $2,
                TRUE
            )
            ON CONFLICT(user_id)
            DO UPDATE SET
            subscription_end=$2,
            is_active=TRUE
            """,
            user_id,
            end_date
        )


async def deactivate_user(
    user_id: int
):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET is_active=FALSE
            WHERE user_id=$1
            """,
            user_id
        )


async def get_all_users():

    async with db_pool.acquire() as conn:

        return await conn.fetch(
            """
            SELECT user_id
            FROM users
            """
        )


logger.info(
    "Part 1 loaded"
)


# =========================================================
#                     PAYMENTS
# =========================================================

async def payment_exists(
    payment_id: str
):

    async with db_pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT payment_id
            FROM payments
            WHERE payment_id=$1
            """,
            payment_id
        )

        return row is not None


async def save_payment(
    payment_id: str,
    user_id: int,
    amount: int,
    payload: str
):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO payments(
                payment_id,
                user_id,
                amount,
                payload
            )
            VALUES(
                $1,
                $2,
                $3,
                $4
            )
            """,
            payment_id,
            user_id,
            amount,
            payload
        )


# =========================================================
#                  CHANNEL ACCESS
# =========================================================

async def check_channel_access():

    try:

        chat = await bot.get_chat(
            CHANNEL_ID
        )

        logger.info(
            f"Channel OK: {chat.title}"
        )

        return True

    except Exception:

        logger.exception(
            "Channel access error"
        )

        return False


# =========================================================
#                       /START
# =========================================================

@dp.message(Command("start"))
async def cmd_start(
    message: types.Message
):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Comprar suscripción",
                    callback_data="buy_subscription"
                )
            ]
        ]
    )

    await message.answer(
        "👋 ¡Bienvenido!\n\n"
        "Este bot proporciona acceso a un canal privado.\n\n"
        f"⭐ Precio: {PRICE_STARS} Stars\n"
        f"📅 Duración: {SUBSCRIPTION_DAYS} días\n\n"
        "Después del pago recibirás un enlace personal para acceder al canal.",
        reply_markup=keyboard
    )


# =========================================================
#                       /MY
# =========================================================

@dp.message(Command("my"))
async def my_subscription(
    message: types.Message
):

    user = await get_user(
        message.from_user.id
    )

    if (
        not user
        or not user["is_active"]
        or not user["subscription_end"]
        or user["subscription_end"] < now()
    ):

        await message.answer(
            "❌ No tienes una suscripción activa.\n\n"
            "Usa /start para comprar una."
        )

        return

    left = (
        user["subscription_end"]
        - now()
    ).days

    await message.answer(
        "✅ Tu suscripción está activa\n\n"
        f"📅 Válida hasta: {format_date(user['subscription_end'])}\n"
        f"⏳ Días restantes: {left}"
    )


# =========================================================
#                 BUY SUBSCRIPTION
# =========================================================

@dp.callback_query(
    F.data == "buy_subscription"
)
async def buy_subscription(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    current_time = time.time()

    last_click = BUY_COOLDOWN.get(
        user_id
    )

    if (
        last_click
        and current_time - last_click < BUY_TIMEOUT
    ):

        await callback.answer(
            "⏳ Espera unos segundos",
            show_alert=True
        )

        return

    BUY_COOLDOWN[user_id] = current_time

    await callback.answer()

    try:

        payload = (
            f"subscription_"
            f"{user_id}_"
            f"{int(current_time)}"
        )

        prices = [
            LabeledPrice(
                label=f"{SUBSCRIPTION_DAYS} días",
                amount=PRICE_STARS
            )
        ]

        await bot.send_invoice(
            chat_id=user_id,
            title="⭐ Suscripción",
            description=(
                f"Acceso durante "
                f"{SUBSCRIPTION_DAYS} días"
            ),
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="subscription"
        )

    except Exception:

        logger.exception(
            "Invoice error"
        )

        await callback.message.answer(
            "❌ Error al crear el pago."
        )


# =========================================================
#               PRE CHECKOUT
# =========================================================

@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    await bot.answer_pre_checkout_query(
        query.id,
        ok=True
    )


# =========================================================
#              SUCCESSFUL PAYMENT
# =========================================================

@dp.message(F.successful_payment)
async def successful_payment(
    message: types.Message
):

    payment = message.successful_payment

    user_id = message.from_user.id

    payment_id = (
        payment.telegram_payment_charge_id
    )

    if await payment_exists(
        payment_id
    ):

        await message.answer(
            "⚠️ Este pago ya fue procesado."
        )

        return

    await save_payment(
        payment_id,
        user_id,
        payment.total_amount,
        payment.invoice_payload
    )

    user = await get_user(
        user_id
    )

    if (
        user
        and user["is_active"]
        and user["subscription_end"]
        and user["subscription_end"] > now()
    ):

        new_end = (
            user["subscription_end"]
            + timedelta(
                days=SUBSCRIPTION_DAYS
            )
        )

    else:

        new_end = (
            now()
            + timedelta(
                days=SUBSCRIPTION_DAYS
            )
        )

    await set_subscription(
        user_id,
        new_end
    )

    try:

        invite = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            name=f"user_{user_id}",
            member_limit=1,
            expire_date=new_end
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📢 Entrar al canal",
                        url=invite.invite_link
                    )
                ]
            ]
        )

        await message.answer(
            "✅ ¡Pago realizado con éxito!\n\n"
            f"📅 Acceso activo hasta:\n"
            f"{format_date(new_end)}\n\n"
            "Pulsa el botón para entrar al canal.",
            reply_markup=keyboard
        )

        logger.info(
            f"Invite sent to {user_id}"
        )

    except TelegramBadRequest:

        logger.exception(
            "Invite link error"
        )

        await message.answer(
            "❌ Pago recibido, pero no se pudo crear el enlace.\n\n"
            "Contacta con el administrador."
        )


logger.info(
    "Part 2 loaded"
)

# =========================================================
#                    STATISTICS
# =========================================================

async def get_statistics():

    async with db_pool.acquire() as conn:

        users = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            """
        )

        active = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE
                is_active=TRUE
                AND subscription_end > NOW()
            """
        )

        payments = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM payments
            """
        )

        stars = await conn.fetchval(
            """
            SELECT COALESCE(
                SUM(amount),
                0
            )
            FROM payments
            """
        )

        expired = users - active

        return {
            "users": users,
            "active": active,
            "expired": expired,
            "payments": payments,
            "stars": stars
        }


async def get_revenue_last_days(
    days: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchval(
            """
            SELECT COALESCE(
                SUM(amount),
                0
            )
            FROM payments
            WHERE created_at >=
            NOW() - ($1 * INTERVAL '1 day')
            """,
            days
        )


async def get_last_payments(
    limit_count: int = 10
):

    async with db_pool.acquire() as conn:

        return await conn.fetch(
            """
            SELECT *
            FROM payments
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit_count
        )


# =========================================================
#                    ADMIN PANEL
# =========================================================

@dp.message(Command("admin"))
async def admin_panel(
    message: types.Message
):

    if not await admin_only(message):
        return

    await message.answer(
        "👑 Панель администратора\n\n"
        "/stats - статистика\n"
        "/revenue - доход\n"
        "/payments - платежи\n"
        "/users - пользователи\n"
        "/check ID - проверить\n"
        "/add ID дни - выдать\n"
        "/extend ID дни - продлить\n"
        "/remove ID - отключить\n"
        "/kick ID - удалить из канала\n"
        "/broadcast текст - рассылка"
    )


# =========================================================
#                       STATS
# =========================================================

@dp.message(Command("stats"))
async def stats_command(
    message: types.Message
):

    if not await admin_only(message):
        return

    data = await get_statistics()

    await message.answer(
        "📊 Статистика\n\n"
        f"👥 Пользователей: {data['users']}\n"
        f"✅ Активных: {data['active']}\n"
        f"❌ Просроченных: {data['expired']}\n\n"
        f"💳 Платежей: {data['payments']}\n"
        f"⭐ Stars: {data['stars']}"
    )


# =========================================================
#                     REVENUE
# =========================================================

@dp.message(Command("revenue"))
async def revenue_command(
    message: types.Message
):

    if not await admin_only(message):
        return

    day = await get_revenue_last_days(1)
    week = await get_revenue_last_days(7)
    month = await get_revenue_last_days(30)

    await message.answer(
        "💰 Доход\n\n"
        f"⭐ За сутки: {day}\n"
        f"⭐ За неделю: {week}\n"
        f"⭐ За месяц: {month}"
    )


# =========================================================
#                    PAYMENTS
# =========================================================

@dp.message(Command("payments"))
async def payments_command(
    message: types.Message
):

    if not await admin_only(message):
        return

    rows = await get_last_payments(10)

    if not rows:

        await message.answer(
            "Платежей пока нет."
        )

        return

    text = "💳 Последние платежи\n\n"

    for row in rows:

        text += (
            f"👤 {row['user_id']}\n"
            f"⭐ {row['amount']}\n"
            f"📅 {format_date(row['created_at'])}\n\n"
        )

    await message.answer(text)


# =========================================================
#                     USERS
# =========================================================

@dp.message(Command("users"))
async def users_command(
    message: types.Message
):

    if not await admin_only(message):
        return

    rows = await get_all_users()

    if not rows:

        await message.answer(
            "База пуста."
        )

        return

    text = "👥 Пользователи\n\n"

    for user in rows[:100]:

        text += (
            f"{user['user_id']}\n"
        )

    if len(rows) > 100:

        text += (
            f"\nЕщё: {len(rows)-100}"
        )

    await message.answer(text)


# =========================================================
#                     CHECK USER
# =========================================================

@dp.message(Command("check"))
async def check_user(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return

    if not command.args:

        await message.answer(
            "Использование:\n/check USER_ID"
        )

        return

    try:

        user_id = int(
            command.args
        )

    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return

    user = await get_user(
        user_id
    )

    if not user:

        await message.answer(
            "Пользователь не найден."
        )

        return

    status = (
        "Активна"
        if user["is_active"]
        else "Отключена"
    )

    await message.answer(
        "👤 Пользователь\n\n"
        f"ID: {user_id}\n"
        f"Статус: {status}\n"
        f"До: {format_date(user['subscription_end'])}"
    )


logger.info(
    "Part 3 loaded"
)

# =========================================================
#                 ADMIN ACTIONS
# =========================================================

@dp.message(Command("add"))
async def add_subscription_command(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return

    try:

        user_id, days = map(
            int,
            command.args.split()
        )

    except Exception:

        await message.answer(
            "Использование:\n/add USER_ID ДНИ"
        )

        return

    end_date = now() + timedelta(
        days=days
    )

    await set_subscription(
        user_id,
        end_date
    )

    await message.answer(
        "✅ Подписка выдана\n\n"
        f"ID: {user_id}\n"
        f"До: {format_date(end_date)}"
    )


# =========================================================
#                    EXTEND
# =========================================================

@dp.message(Command("extend"))
async def extend_subscription_command(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return

    try:

        user_id, days = map(
            int,
            command.args.split()
        )

    except Exception:

        await message.answer(
            "Использование:\n/extend USER_ID ДНИ"
        )

        return

    user = await get_user(
        user_id
    )

    if (
        user
        and user["subscription_end"]
        and user["subscription_end"] > now()
    ):

        new_end = (
            user["subscription_end"]
            + timedelta(days=days)
        )

    else:

        new_end = (
            now()
            + timedelta(days=days)
        )

    await set_subscription(
        user_id,
        new_end
    )

    await message.answer(
        "✅ Подписка продлена\n\n"
        f"ID: {user_id}\n"
        f"До: {format_date(new_end)}"
    )


# =========================================================
#                   REMOVE
# =========================================================

@dp.message(Command("remove"))
async def remove_subscription_command(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return

    if not command.args:

        await message.answer(
            "Использование:\n/remove USER_ID"
        )

        return

    try:

        user_id = int(
            command.args
        )

    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return

    await deactivate_user(
        user_id
    )

    await message.answer(
        f"❌ Подписка отключена: {user_id}"
    )


# =========================================================
#               KICK FROM CHANNEL
# =========================================================

async def kick_from_channel(
    user_id: int
):

    try:

        member = await bot.get_chat_member(
            CHANNEL_ID,
            user_id
        )

        if member.status in (
            "administrator",
            "creator"
        ):

            logger.warning(
                f"Попытка удалить администратора {user_id}"
            )

            return False

        await bot.ban_chat_member(
            CHANNEL_ID,
            user_id
        )

        await bot.unban_chat_member(
            CHANNEL_ID,
            user_id
        )

        return True

    except TelegramBadRequest:

        logger.exception(
            f"Ошибка удаления {user_id}"
        )

        return False

    except Exception:

        logger.exception(
            "Kick error"
        )

        return False


# =========================================================
#                     /KICK
# =========================================================

@dp.message(Command("kick"))
async def kick_command(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return

    if not command.args:

        await message.answer(
            "Использование:\n/kick USER_ID"
        )

        return

    try:

        user_id = int(
            command.args
        )

    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return

    result = await kick_from_channel(
        user_id
    )

    if result:

        await message.answer(
            "✅ Пользователь удалён из канала."
        )

    else:

        await message.answer(
            "⚠️ Не удалось удалить пользователя."
        )


# =========================================================
#                   BROADCAST
# =========================================================

@dp.message(Command("broadcast"))
async def broadcast_command(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return

    if not command.args:

        await message.answer(
            "Использование:\n/broadcast текст"
        )

        return

    users = await get_all_users()

    success = 0
    failed = 0

    status_message = await message.answer(
        f"📨 Начинаю рассылку...\nПолучателей: {len(users)}"
    )

    for user in users:

        user_id = user["user_id"]

        try:

            await bot.send_message(
                user_id,
                command.args
            )

            success += 1

            await asyncio.sleep(
                0.05
            )

        except TelegramForbiddenError:

            failed += 1

        except TelegramRetryAfter as e:

            await asyncio.sleep(
                e.retry_after
            )

            try:

                await bot.send_message(
                    user_id,
                    command.args
                )

                success += 1

            except Exception:

                failed += 1

        except Exception:

            failed += 1

    await status_message.edit_text(
        "✅ Рассылка завершена\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {failed}"
    )


logger.info(
    "Part 4 loaded"
)

# =========================================================
#              EXPIRED SUBSCRIPTIONS
# =========================================================

async def check_expired_subscriptions():

    logger.info(
        "Checking expired subscriptions..."
    )

    async with db_pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT user_id
            FROM users
            WHERE
                is_active=TRUE
                AND subscription_end < NOW()
            """
        )

    removed = 0

    for row in rows:

        user_id = row["user_id"]

        try:

            await kick_from_channel(
                user_id
            )

            await deactivate_user(
                user_id
            )

            removed += 1

        except Exception:

            logger.exception(
                f"Expired user error: {user_id}"
            )

    logger.info(
        f"Expired processed: {removed}"
    )


# =========================================================
#                    REMINDERS
# =========================================================

async def send_reminders():

    async with db_pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT
                user_id,
                subscription_end
            FROM users
            WHERE is_active=TRUE
            """
        )

    for row in rows:

        end_date = row["subscription_end"]

        if not end_date:
            continue

        days_left = (
            end_date - now()
        ).days

        text = None

        if days_left == 7:

            text = (
                "🔔 Tu suscripción termina en 7 días.\n\n"
                "Renueva el acceso con /start."
            )

        elif days_left == 3:

            text = (
                "⏳ Tu suscripción termina en 3 días."
            )

        elif days_left == 1:

            text = (
                "⚠️ Tu suscripción termina mañana."
            )

        elif days_left == 0:

            text = (
                "❗ Hoy es el último día de tu suscripción."
            )

        if not text:
            continue

        try:

            await bot.send_message(
                row["user_id"],
                text
            )

            await asyncio.sleep(
                0.05
            )

        except Exception:

            logger.exception(
                "Reminder error"
            )


# =========================================================
#                 DATABASE HEALTH
# =========================================================

async def database_health():

    try:

        async with db_pool.acquire() as conn:

            await conn.fetchval(
                "SELECT 1"
            )

        return True

    except Exception:

        logger.exception(
            "Database health failed"
        )

        return False


# =========================================================
#                    HTTP SERVER
# =========================================================

class HealthHandler(
    http.server.BaseHTTPRequestHandler
):

    def do_GET(self):

        global LAST_PING

        if self.path == "/":

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"BOT ONLINE"
            )

            return

        if self.path == "/health":

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"OK"
            )

            return

        if self.path == "/ping":

            LAST_PING = now()

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"PONG"
            )

            return

        if self.path == "/metrics":

            uptime = int(
                (
                    now()
                    - START_TIME
                ).total_seconds()
            )

            payload = {
                "uptime_seconds": uptime,
                "last_ping": (
                    str(LAST_PING)
                    if LAST_PING
                    else None
                )
            }

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                json.dumps(
                    payload
                ).encode()
            )

            return

        self.send_response(404)
        self.end_headers()

    def log_message(
        self,
        format,
        *args
    ):
        return


# =========================================================
#                HTTP THREAD START
# =========================================================

def run_http_server():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    with socketserver.TCPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    ) as server:

        logger.info(
            f"HTTP server started on {port}"
        )

        server.serve_forever()


# =========================================================
#                  SYSTEM MONITOR
# =========================================================

async def monitor_system():

    while True:

        try:

            await database_health()

        except Exception:

            logger.exception(
                "Monitor error"
            )

        await asyncio.sleep(
            1800
        )


# =========================================================
#                SCHEDULER JOBS
# =========================================================

def register_scheduler_jobs():

    scheduler.add_job(
        check_expired_subscriptions,
        "interval",
        hours=1
    )

    scheduler.add_job(
        send_reminders,
        CronTrigger(
            hour=10,
            minute=0
        )
    )

    logger.info(
        "Scheduler jobs loaded"
    )


logger.info(
    "Part 5 loaded"
)

# =========================================================
#                  STARTUP CHECKS
# =========================================================

async def startup_checks():

    logger.info(
        "Running startup checks..."
    )

    db_ok = await database_health()

    if not db_ok:

        raise RuntimeError(
            "Database unavailable"
        )

    channel_ok = await check_channel_access()

    if not channel_ok:

        logger.warning(
            "Bot has no access to channel"
        )

    logger.info(
        "Startup checks passed"
    )


# =========================================================
#                     SHUTDOWN
# =========================================================

async def shutdown():

    logger.info(
        "Shutdown started"
    )

    try:

        if scheduler.running:

            scheduler.shutdown(
                wait=False
            )

    except Exception:

        logger.exception(
            "Scheduler shutdown error"
        )

    try:

        global db_pool

        if db_pool:

            await db_pool.close()

    except Exception:

        logger.exception(
            "Pool close error"
        )

    try:

        await bot.session.close()

    except Exception:

        logger.exception(
            "Bot session close error"
        )


# =========================================================
#                       MAIN
# =========================================================

async def main():

    logger.info(
        "Starting Telegram Subscription Bot V3"
    )

    await init_database()

    # запускаем HTTP сервер Render
    http_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    http_thread.start()

    # убираем старый webhook
    try:

        await bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info(
            "Webhook deleted"
        )

    except Exception:

        logger.exception(
            "Webhook delete failed"
        )

    await startup_checks()

    register_scheduler_jobs()

    scheduler.start()

    logger.info(
        "Scheduler started"
    )

    asyncio.create_task(
        monitor_system()
    )

    logger.info(
        "Bot ready"
    )

    try:

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )

    finally:

        await shutdown()


# =========================================================
#                    ENTRY POINT
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Stopped manually"
        )

    except Exception:

        logger.exception(
            "Fatal startup error"
        )
