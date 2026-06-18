# =========================================================
#                 TELEGRAM SUBSCRIPTION BOT V2 PRO
#                    Render + PostgreSQL + Stars
# =========================================================

import asyncio
import logging
import os
import threading
import http.server
import socketserver
import time
import traceback
import platform
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
#                      ENV
# =========================================================

load_dotenv()


def get_env(name: str) -> str:

    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"❌ Не найден ENV параметр: {name}"
        )

    return value


BOT_TOKEN = get_env("BOT_TOKEN")

DATABASE_URL = get_env(
    "DATABASE_URL"
)

CHANNEL_ID = get_env(
    "CHANNEL_ID"
)

ADMIN_ID = int(
    get_env("ADMIN_ID")
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
#                      LOGGING
# =========================================================


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )
)


logger = logging.getLogger(
    "subscription_bot"
)


logger.info(
    "Загрузка конфигурации..."
)


# =========================================================
#               ГЛОБАЛЬНЫЕ ОБЪЕКТЫ
# =========================================================


bot = Bot(
    token=BOT_TOKEN
)


dp = Dispatcher()


scheduler = AsyncIOScheduler()


db_pool: Optional[asyncpg.Pool] = None


# =========================================================
#          МОНИТОРИНГ СЕРВЕРА
# =========================================================


START_TIME = datetime.now(
    timezone.utc
)


LAST_PING = None


LAST_DATABASE_CHECK = None


# =========================================================
#               ЗАЩИТА ОТ СПАМА
# =========================================================

# user_id: timestamp
BUY_COOLDOWN = {}


BUY_TIMEOUT = 5


# =========================================================
#          ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================


def now() -> datetime:
    """
    Текущее время UTC
    """

    return datetime.now(
        timezone.utc
    )


def format_date(
    value: datetime
) -> str:

    if not value:
        return "Не указано"


    return value.strftime(
        "%d.%m.%Y %H:%M UTC"
    )


def human_time(
    seconds: int
) -> str:

    days = seconds // 86400

    seconds %= 86400


    hours = seconds // 3600

    seconds %= 3600


    minutes = seconds // 60


    result = []


    if days:
        result.append(
            f"{days}д"
        )


    if hours:
        result.append(
            f"{hours}ч"
        )


    if minutes:
        result.append(
            f"{minutes}м"
        )


    if not result:
        return "меньше минуты"


    return " ".join(result)


# =========================================================
#              ПРОВЕРКА ДОСТУПА АДМИНА
# =========================================================


async def admin_only(
    message: types.Message
) -> bool:

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
#                 СТАРТ СИСТЕМЫ
# =========================================================


logger.info(
    "Конфигурация успешно загружена"
)

# =========================================================
#                  POSTGRESQL DATABASE
# =========================================================


async def init_database():
    """
    Создание пула соединений и подготовка базы
    """

    global db_pool
    global LAST_DATABASE_CHECK


    logger.info(
        "Создание PostgreSQL пула..."
    )


    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        command_timeout=30
    )


    async with db_pool.acquire() as conn:

        # Пользователи
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                subscription_end_date TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)


        # Оплаты
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                payload TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)


        # Индексы для ускорения запросов
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_active
            ON users(is_active)
        """)


        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_subscription
            ON users(subscription_end_date)
        """)


        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_user
            ON payments(user_id)
        """)


    LAST_DATABASE_CHECK = now()


    logger.info(
        "PostgreSQL успешно подключён"
    )


# =========================================================
#                    ПОЛЬЗОВАТЕЛИ
# =========================================================


async def get_user(
    user_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT *
            FROM users
            WHERE user_id = $1
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
            INSERT INTO users
                (
                    user_id,
                    subscription_end_date,
                    is_active
                )
            VALUES
                (
                    $1,
                    $2,
                    TRUE
                )

            ON CONFLICT (user_id)
            DO UPDATE SET
                subscription_end_date = $2,
                is_active = TRUE
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
            SET is_active = FALSE
            WHERE user_id = $1
            """,
            user_id
        )


async def delete_user(
    user_id: int
):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM users
            WHERE user_id = $1
            """,
            user_id
        )


# =========================================================
#                     ПЛАТЕЖИ
# =========================================================


async def payment_exists(
    payment_id: str
) -> bool:

    async with db_pool.acquire() as conn:

        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM payments
                WHERE payment_id = $1
            )
            """,
            payment_id
        )

        return result


async def save_payment(
    payment_id: str,
    user_id: int,
    amount: int,
    payload: str
):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO payments
                (
                    payment_id,
                    user_id,
                    amount,
                    payload
                )
            VALUES
                (
                    $1,
                    $2,
                    $3,
                    $4
                )
            ON CONFLICT DO NOTHING
            """,
            payment_id,
            user_id,
            amount,
            payload
        )


async def get_last_payments(
    limit: int = 10
):

    async with db_pool.acquire() as conn:

        return await conn.fetch(
            """
            SELECT *
            FROM payments
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit
        )


# =========================================================
#                     СТАТИСТИКА
# =========================================================


async def get_statistics():

    async with db_pool.acquire() as conn:


        total_users = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            """
        )


        active_users = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE is_active = TRUE
            AND subscription_end_date > NOW()
            """
        )


        expired_users = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE
                subscription_end_date < NOW()
            """
        )


        total_stars = await conn.fetchval(
            """
            SELECT COALESCE(
                SUM(amount),
                0
            )
            FROM payments
            """
        )


        payments_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM payments
            """
        )


    return {
        "users": total_users,
        "active": active_users,
        "expired": expired_users,
        "payments": payments_count,
        "stars": total_stars
    }


# =========================================================
#              ДОПОЛНИТЕЛЬНЫЕ ЗАПРОСЫ
# =========================================================


async def get_all_users():

    async with db_pool.acquire() as conn:

        return await conn.fetch(
            """
            SELECT user_id
            FROM users
            """
        )


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
            WHERE created_at >= NOW() - ($1 * INTERVAL '1 day')
            """,
            days
        )


# =========================================================
#                  ПРОВЕРКА БАЗЫ
# =========================================================


async def database_health():

    global LAST_DATABASE_CHECK


    try:

        async with db_pool.acquire() as conn:

            await conn.execute(
                "SELECT 1"
            )


        LAST_DATABASE_CHECK = now()


        return True


    except Exception:

        logger.exception(
            "Ошибка проверки базы данных"
        )

        return False


logger.info(
    "Модуль базы данных загружен"
)

# =========================================================
#                   HTTP SERVER / RENDER
# =========================================================

import json
import sys


# =========================================================
#                ИНФОРМАЦИЯ О СИСТЕМЕ
# =========================================================


def get_system_info():

    uptime_seconds = int(
        (now() - START_TIME).total_seconds()
    )

    return {
        "status": "ok",
        "bot_uptime": human_time(
            uptime_seconds
        ),
        "python": sys.version,
        "platform": platform.platform(),
        "last_ping": (
            format_date(LAST_PING)
            if LAST_PING
            else "никогда"
        ),
        "database": (
            "online"
            if LAST_DATABASE_CHECK
            else "unknown"
        )
    }


# =========================================================
#                  HTTP HANDLER
# =========================================================


class HealthHandler(
    http.server.BaseHTTPRequestHandler
):

    def do_GET(self):

        global LAST_PING


        # Главная страница
        if self.path == "/":

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "application/json"
            )

            self.end_headers()

            self.wfile.write(
                json.dumps(
                    get_system_info()
                ).encode()
            )

            return


        # Render Health Check
        if self.path == "/health":

            self.send_response(200)

            self.end_headers()

            self.wfile.write(
                b"OK"
            )

            return


        # Cron ping
        if self.path == "/ping":

            LAST_PING = now()

            logger.info(
                "Получен cron ping"
            )

            self.send_response(200)

            self.end_headers()

            self.wfile.write(
                b"PONG"
            )

            return


        # Метрики сервера
        if self.path == "/metrics":

            data = get_system_info()


            self.send_response(200)

            self.send_header(
                "Content-Type",
                "application/json"
            )

            self.end_headers()

            self.wfile.write(
                json.dumps(
                    data,
                    indent=4,
                    ensure_ascii=False
                ).encode("utf-8")
            )

            return


        # Всё остальное
        self.send_response(404)

        self.end_headers()


    # Убираем мусорные логи
    def log_message(
        self,
        format,
        *args
    ):
        return


# =========================================================
#                ЗАПУСК HTTP СЕРВЕРА
# =========================================================


def run_http_server():

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )


    server = socketserver.TCPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )


    logger.info(
        f"HTTP сервер запущен на порту {port}"
    )


    try:

        server.serve_forever()


    except Exception:

        logger.exception(
            "Ошибка HTTP сервера"
        )


    finally:

        server.server_close()

        logger.info(
            "HTTP сервер остановлен"
        )


# =========================================================
#                ДОПОЛНИТЕЛЬНЫЙ МОНИТОРИНГ
# =========================================================


async def monitor_system():

    while True:

        try:

            db_status = (
                await database_health()
            )


            logger.info(
                "SYSTEM CHECK | "
                f"DB: {'OK' if db_status else 'ERROR'} | "
                f"UPTIME: {human_time(int((now() - START_TIME).total_seconds()))}"
            )


        except Exception:

            logger.exception(
                "Ошибка мониторинга системы"
            )


        # Проверяем каждые 10 минут
        await asyncio.sleep(
            600
        )


logger.info(
    "HTTP и мониторинг загружены"
)

# =========================================================
#                 ПРОВЕРКА КАНАЛА
# =========================================================


async def check_channel_access():

    try:

        chat = await bot.get_chat(
            CHANNEL_ID
        )

        logger.info(
            f"Канал найден: {chat.title} ({chat.id})"
        )

        return True


    except Exception:

        logger.exception(
            "Бот не имеет доступа к каналу"
        )

        return False


# =========================================================
#                      /START
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
#                 КНОПКА ПОКУПКИ
# =========================================================


@dp.callback_query(
    F.data == "buy_subscription"
)
async def buy_subscription(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    current_time = time.time()


    # Антиспам нажатий
    last_click = BUY_COOLDOWN.get(
        user_id
    )


    if (
        last_click
        and current_time - last_click < BUY_TIMEOUT
    ):

        await callback.answer(
            "⏳ Подождите несколько секунд",
            show_alert=True
        )

        return


    BUY_COOLDOWN[user_id] = current_time


    await callback.answer(
        "Создаём счёт..."
    )


    try:

        payload = (
            f"subscription_"
            f"{user_id}_"
            f"{int(current_time)}"
        )


        prices = [
            LabeledPrice(
                label=f"Доступ на {SUBSCRIPTION_DAYS} дней",
                amount=PRICE_STARS
            )
        ]


        await bot.send_invoice(
            chat_id=user_id,
            title="⭐ Подписка на канал",
            description=(
                f"Доступ на "
                f"{SUBSCRIPTION_DAYS} дней"
            ),
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="subscription"
        )


        logger.info(
            f"Создан счёт для пользователя {user_id}"
        )


    except Exception:

        logger.exception(
            "Ошибка создания счёта"
        )


        await callback.message.answer(
            "❌ Не удалось создать оплату.\n"
            "Попробуйте позже."
        )


# =========================================================
#              ПОДТВЕРЖДЕНИЕ ОПЛАТЫ
# =========================================================


@dp.pre_checkout_query()
async def process_pre_checkout(
    query: PreCheckoutQuery
):

    await bot.answer_pre_checkout_query(
        query.id,
        ok=True
    )


# =========================================================
#                 УСПЕШНАЯ ОПЛАТА
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


    # Защита от повторной обработки
    if await payment_exists(
        payment_id
    ):

        logger.warning(
            f"Повторный платёж: {payment_id}"
        )

        await message.answer(
            "⚠️ Этот платёж уже обработан."
        )

        return


    # Сохраняем оплату
    await save_payment(
        payment_id,
        user_id,
        payment.total_amount,
        payment.invoice_payload
    )


    current_user = await get_user(
        user_id
    )


    current_time = now()


    # Продление подписки
    if (
        current_user
        and current_user["is_active"]
        and current_user["subscription_end_date"]
        and current_user["subscription_end_date"] > current_time
    ):

        new_end = (
            current_user["subscription_end_date"]
            + timedelta(
                days=SUBSCRIPTION_DAYS
            )
        )

        logger.info(
            f"Продлена подписка {user_id}"
        )


    else:

        new_end = (
            current_time
            + timedelta(
                days=SUBSCRIPTION_DAYS
            )
        )

        logger.info(
            f"Новая подписка {user_id}"
        )


    await set_subscription(
        user_id,
        new_end
    )


# =========================================================
#            СОЗДАНИЕ ССЫЛКИ НА КАНАЛ
# =========================================================

try:

    invite = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name=f"user_{user_id}",
        expire_date=new_end,
        member_limit=1
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
        "Pulsa el botón para entrar al canal:",
        reply_markup=keyboard
    )

    logger.info(
        f"Выдана ссылка пользователю {user_id}"
    )

except TelegramBadRequest:

        logger.exception(
            "Ошибка создания ссылки"
        )


        await message.answer(
            "❌ Оплата получена, но ссылка "
            "не была создана.\n\n"
            "Сообщите администратору."
        )


    except Exception:

        logger.exception(
            "Неизвестная ошибка выдачи ссылки"
        )


        await message.answer(
            "❌ Внутренняя ошибка сервера."
        )


# =========================================================
#                 ПРОВЕРКА ПОДПИСКИ
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
        or not user["subscription_end_date"]
        or user["subscription_end_date"] < now()
    ):

        await message.answer(
            "❌ No tienes una suscripción activa.\n\n"
            "Usa /start para comprar una."
        )

        return


    left = (
        user["subscription_end_date"]
        - now()
    ).days


    await message.answer(
        "✅ Tu suscripción está activa\n\n"
        f"📅 Válida hasta: {format_date(user['subscription_end_date'])}\n"
        f"⏳ Días restantes: {left}"
    )


logger.info(
    "Пользовательские команды и платежи загружены"
)

# =========================================================
#                    АДМИН ПАНЕЛЬ
# =========================================================


@dp.message(Command("admin"))
async def admin_panel(
    message: types.Message
):

    if not await admin_only(message):
        return


    await message.answer(
        "👑 Панель администратора\n\n"
        "Команды:\n\n"
        "/stats — статистика\n"
        "/revenue — доход\n"
        "/payments — последние оплаты\n"
        "/users — список пользователей\n"
        "/check ID — проверить пользователя\n"
        "/add ID дни — выдать подписку\n"
        "/extend ID дни — продлить подписку\n"
        "/remove ID — удалить подписку\n"
        "/kick ID — удалить из канала"
    )


# =========================================================
#                     СТАТИСТИКА
# =========================================================


@dp.message(Command("stats"))
async def stats(
    message: types.Message
):

    if not await admin_only(message):
        return


    data = await get_statistics()


    await message.answer(
        "📊 Статистика бота\n\n"
        f"👥 Всего пользователей: {data['users']}\n"
        f"✅ Активных подписок: {data['active']}\n"
        f"❌ Просроченных: {data['expired']}\n\n"
        f"💳 Оплат: {data['payments']}\n"
        f"⭐ Получено Stars: {data['stars']}"
    )


# =========================================================
#                    ДОХОД
# =========================================================


@dp.message(Command("revenue"))
async def revenue(
    message: types.Message
):

    if not await admin_only(message):
        return


    day = await get_revenue_last_days(1)
    month = await get_revenue_last_days(30)


    await message.answer(
        "💰 Доход\n\n"
        f"⭐ За сутки: {day}\n"
        f"⭐ За 30 дней: {month}"
    )


# =========================================================
#                 ПОСЛЕДНИЕ ПЛАТЕЖИ
# =========================================================


@dp.message(Command("payments"))
async def payments(
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


    text = "💳 Последние платежи:\n\n"


    for p in rows:

        text += (
            f"👤 {p['user_id']}\n"
            f"⭐ {p['amount']} Stars\n"
            f"📅 {format_date(p['created_at'])}\n\n"
        )


    await message.answer(text)


# =========================================================
#              СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# =========================================================


@dp.message(Command("users"))
async def users(
    message: types.Message
):

    if not await admin_only(message):
        return


    rows = await get_all_users()


    if not rows:

        await message.answer(
            "База пользователей пуста."
        )

        return


    text = "👥 Пользователи:\n\n"


    for user in rows[:100]:

        text += (
            f"🆔 {user['user_id']}\n"
        )


    if len(rows) > 100:

        text += (
            f"\nИ ещё {len(rows)-100} пользователей..."
        )


    await message.answer(text)


# =========================================================
#              ПРОВЕРКА ПОЛЬЗОВАТЕЛЯ
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

        user_id = int(command.args)

    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return


    user = await get_user(user_id)


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
        f"До: {format_date(user['subscription_end_date'])}"
    )


# =========================================================
#              ВЫДАЧА ПОДПИСКИ
# =========================================================


@dp.message(Command("add"))
async def add_subscription(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    try:

        uid, days = map(
            int,
            command.args.split()
        )

    except Exception:

        await message.answer(
            "Использование:\n/add USER_ID ДНИ"
        )

        return


    end = now() + timedelta(days=days)


    await set_subscription(
        uid,
        end
    )


    await message.answer(
        f"✅ Подписка выдана\n"
        f"ID: {uid}\n"
        f"До: {format_date(end)}"
    )


# =========================================================
#           ПРОДОЛЖЕНИЕ В ЧАСТИ 6/8
# =========================================================

# =========================================================
#              ПРОДЛЕНИЕ ПОДПИСКИ
# =========================================================


@dp.message(Command("extend"))
async def extend_subscription(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    try:

        uid, days = map(
            int,
            command.args.split()
        )

    except Exception:

        await message.answer(
            "Использование:\n/extend USER_ID ДНИ"
        )
        return


    user = await get_user(uid)


    if (
        user
        and user["is_active"]
        and user["subscription_end_date"]
        and user["subscription_end_date"] > now()
    ):

        end_date = (
            user["subscription_end_date"]
            + timedelta(days=days)
        )

    else:

        end_date = (
            now()
            + timedelta(days=days)
        )


    await set_subscription(
        uid,
        end_date
    )


    await message.answer(
        "✅ Подписка продлена\n\n"
        f"ID: {uid}\n"
        f"До: {format_date(end_date)}"
    )


# =========================================================
#              УДАЛЕНИЕ ПОДПИСКИ
# =========================================================


@dp.message(Command("remove"))
async def remove_subscription(
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
        uid = int(command.args)

    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )
        return


    await deactivate_user(uid)


    await message.answer(
        f"❌ Подписка пользователя {uid} отключена."
    )


# =========================================================
#              УДАЛЕНИЕ ИЗ КАНАЛА
# =========================================================


async def kick_from_channel(
    user_id: int
):

    try:

        member = await bot.get_chat_member(
            CHANNEL_ID,
            user_id
        )


        # Защита админов канала
        if member.status in [
            "administrator",
            "creator"
        ]:

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


        logger.info(
            f"Пользователь {user_id} удалён из канала"
        )


        return True


    except TelegramBadRequest as e:

        logger.warning(
            f"Ошибка удаления {user_id}: {e}"
        )

        return False


    except Exception:

        logger.exception(
            "Ошибка удаления из канала"
        )

        return False


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

        uid = int(command.args)

    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return


    result = await kick_from_channel(uid)


    if result:

        await message.answer(
            "✅ Пользователь удалён из канала."
        )

    else:

        await message.answer(
            "⚠️ Не удалось удалить пользователя."
        )


# =========================================================
#                   РАССЫЛКА
# =========================================================


@dp.message(Command("broadcast"))
async def broadcast(
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


    total = len(users)
    success = 0
    failed = 0


    status_message = await message.answer(
        f"📨 Начинаю рассылку\n"
        f"Получателей: {total}"
    )


    for user in users:

        uid = user["user_id"]


        try:

            await bot.send_message(
                uid,
                command.args
            )

            success += 1


            # Защита от лимитов Telegram
            await asyncio.sleep(0.05)


        except TelegramForbiddenError:

            failed += 1


        except TelegramRetryAfter as e:

            logger.warning(
                f"Flood control: ждём {e.retry_after}"
            )

            await asyncio.sleep(
                e.retry_after
            )

            try:

                await bot.send_message(
                    uid,
                    command.args
                )

                success += 1

            except Exception:

                failed += 1


        except Exception:

            logger.exception(
                f"Ошибка отправки {uid}"
            )

            failed += 1


    await status_message.edit_text(
        "✅ Рассылка завершена\n\n"
        f"Успешно: {success}\n"
        f"Ошибок: {failed}"
    )


# =========================================================
#              КОНЕЦ АДМИН-ПАНЕЛИ
# =========================================================


logger.info(
    "Админ-панель загружена"
)

# =========================================================
#              УВЕДОМЛЕНИЕ АДМИНИСТРАТОРА
# =========================================================


async def notify_admin(text: str):

    try:

        await bot.send_message(
            ADMIN_ID,
            f"⚠️ Системное уведомление\n\n{text}"
        )

    except Exception:

        logger.exception(
            "Не удалось отправить сообщение администратору"
        )


# =========================================================
#          ПРОВЕРКА ПРОСРОЧЕННЫХ ПОДПИСОК
# =========================================================


async def check_expired_subscriptions():

    logger.info(
        "Проверка просроченных подписок..."
    )


    try:

        async with db_pool.acquire() as conn:

            users = await conn.fetch(
                """
                SELECT user_id
                FROM users
                WHERE
                    is_active = TRUE
                    AND subscription_end_date < NOW()
                """
            )


        removed = 0


        for user in users:

            user_id = user["user_id"]


            try:

                # Удаляем из канала
                await kick_from_channel(
                    user_id
                )


                # Отключаем подписку
                await deactivate_user(
                    user_id
                )


                removed += 1


            except Exception:

                logger.exception(
                    f"Ошибка обработки пользователя {user_id}"
                )


        logger.info(
            f"Проверка завершена. Отключено: {removed}"
        )


    except Exception:

        logger.exception(
            "Ошибка проверки подписок"
        )

        await notify_admin(
            "Ошибка при проверке просроченных подписок"
        )


# =========================================================
#              НАПОМИНАНИЯ О ПРОДЛЕНИИ
# =========================================================


async def send_reminders():

    logger.info(
        "Отправка напоминаний..."
    )


    try:

        async with db_pool.acquire() as conn:

            users = await conn.fetch(
                """
                SELECT user_id, subscription_end_date
                FROM users
                WHERE is_active = TRUE
                """
            )


        sent = 0


        for user in users:

            end_date = (
                user["subscription_end_date"]
            )


            if not end_date:
                continue


            days_left = (
                end_date - now()
            ).days


            text = None


            if days_left == 7:

                text = (
                    "🔔 Ваша подписка закончится через 7 дней.\n\n"
                    "Продлите её заранее через /start"
                )


            elif days_left == 3:

                text = (
                    "⏳ До окончания подписки осталось 3 дня.\n\n"
                    "Не забудьте продлить доступ."
                )


            elif days_left == 1:

                text = (
                    "⚠️ Завтра ваша подписка закончится.\n\n"
                    "Продлите доступ, чтобы не потерять канал."
                )


            elif days_left == 0:

                text = (
                    "❗ Сегодня последний день вашей подписки."
                )


            if text:

                try:

                    await bot.send_message(
                        user["user_id"],
                        text
                    )


                    sent += 1


                    await asyncio.sleep(
                        0.05
                    )


                except TelegramForbiddenError:

                    logger.warning(
                        f"Пользователь {user['user_id']} заблокировал бота"
                    )


                except Exception:

                    logger.exception(
                        f"Ошибка отправки напоминания {user['user_id']}"
                    )


        logger.info(
            f"Напоминаний отправлено: {sent}"
        )


    except Exception:

        logger.exception(
            "Ошибка напоминаний"
        )

        await notify_admin(
            "Ошибка отправки напоминаний"
        )


# =========================================================
#             ПРОВЕРКА ДОСТУПА К КАНАЛУ
# =========================================================


async def periodic_channel_check():

    try:

        status = await check_channel_access()


        if not status:

            await notify_admin(
                "Бот потерял доступ к каналу.\n"
                "Проверьте права администратора."
            )


    except Exception:

        logger.exception(
            "Ошибка проверки канала"
        )


# =========================================================
#             ОБЩАЯ САМОДИАГНОСТИКА
# =========================================================


async def system_diagnostics():

    while True:

        try:

            logger.info(
                "SYSTEM STATUS | "
                f"UPTIME: {human_time(int((now() - START_TIME).total_seconds()))}"
            )


            # Проверяем базу
            await database_health()


            # Проверяем канал
            await periodic_channel_check()


        except Exception:

            logger.exception(
                "Ошибка диагностики"
            )


        # Раз в 30 минут
        await asyncio.sleep(
            1800
        )


# =========================================================
#             ЗАЩИЩЕННЫЕ ЗАДАЧИ SCHEDULER
# =========================================================


async def safe_job(job, name):

    try:

        await job()


    except Exception:

        logger.exception(
            f"Ошибка задачи {name}"
        )

        await notify_admin(
            f"Задача {name} завершилась с ошибкой"
        )


def register_scheduler_jobs():

    scheduler.add_job(
        lambda: asyncio.create_task(
            safe_job(
                check_expired_subscriptions,
                "check_expired_subscriptions"
            )
        ),
        "interval",
        hours=1
    )


    scheduler.add_job(
        lambda: asyncio.create_task(
            safe_job(
                send_reminders,
                "send_reminders"
            )
        ),
        CronTrigger(
            hour=10,
            minute=0
        )
    )


    logger.info(
        "Задачи планировщика зарегистрированы"
    )

# =========================================================
#                     ЗАПУСК СИСТЕМЫ
# =========================================================


async def startup_checks():

    logger.info(
        "Запуск стартовых проверок..."
    )


    # Проверяем доступ к каналу
    channel_ok = await check_channel_access()


    if not channel_ok:

        await notify_admin(
            "Бот запущен, но не имеет доступа к каналу."
        )


    # Проверяем базу
    db_ok = await database_health()


    if not db_ok:

        raise RuntimeError(
            "База данных недоступна"
        )


    logger.info(
        "Стартовые проверки завершены"
    )


# =========================================================
#                     ОСТАНОВКА
# =========================================================


async def shutdown():

    logger.info(
        "Начинается завершение работы..."
    )


    # Останавливаем планировщик
    try:

        if scheduler.running:

            scheduler.shutdown(
                wait=False
            )

            logger.info(
                "Scheduler остановлен"
            )

    except Exception:

        logger.exception(
            "Ошибка остановки scheduler"
        )


    # Закрываем PostgreSQL pool
    try:

        global db_pool

        if db_pool:

            await db_pool.close()

            logger.info(
                "PostgreSQL pool закрыт"
            )

    except Exception:

        logger.exception(
            "Ошибка закрытия базы"
        )


    # Закрываем сессию бота
    try:

        await bot.session.close()

        logger.info(
            "Telegram session закрыта"
        )

    except Exception:

        logger.exception(
            "Ошибка закрытия Telegram"
        )


# =========================================================
#                       MAIN
# =========================================================


async def main():

    logger.info(
        "Запуск Telegram Subscription Bot V2 PRO"
    )


    # Подключаем PostgreSQL
    await init_database()


    # Запускаем HTTP сервер Render
    http_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    http_thread.start()


    # Удаляем старый webhook
    try:

        await bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info(
            "Webhook очищен"
        )

    except Exception:

        logger.exception(
            "Ошибка очистки webhook"
        )


    # Проверяем систему
    await startup_checks()


    # Запускаем задачи планировщика
    register_scheduler_jobs()

    scheduler.start()

    logger.info(
        "Scheduler запущен"
    )


    # Фоновый мониторинг
    asyncio.create_task(
        system_diagnostics()
    )


    asyncio.create_task(
        monitor_system()
    )


    logger.info(
        "Бот готов к работе"
    )


    # Запуск polling
    try:

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )


    finally:

        await shutdown()


# =========================================================
#                     ТОЧКА ВХОДА
# =========================================================


if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Бот остановлен вручную"
        )

    except Exception:

        logger.exception(
            "Критическая ошибка запуска"
        )
        
