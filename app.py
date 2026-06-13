import asyncio
import logging
import os
import threading
import http.server
import socketserver

from datetime import datetime, timedelta
from typing import Optional

import asyncpg
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    LabeledPrice,
    PreCheckoutQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


# ==================================================
# ЗАГРУЗКА ENV
# ==================================================

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Переменная {name} не найдена!"
        )

    return value


BOT_TOKEN = require_env("BOT_TOKEN")
CHANNEL_ID = int(require_env("CHANNEL_ID"))
DATABASE_URL = require_env("DATABASE_URL")
ADMIN_ID = int(require_env("ADMIN_ID"))

BOT_START_TIME = datetime.now().astimezone()
LAST_PING = None

PRICE_STARS = int(
    os.getenv("PRICE_STARS", "15")
)

SUBSCRIPTION_DAYS = 30


# ==================================================
# ЛОГИ
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ==================================================
# BOT
# ==================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()

scheduler = AsyncIOScheduler()


# ==================================================
# PostgreSQL POOL
# ==================================================

db_pool: Optional[asyncpg.Pool] = None


async def init_db():

    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10
    )

    logger.info(
        "Пул PostgreSQL создан"
    )

    async with db_pool.acquire() as conn:

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                subscription_end_date TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                payload TEXT,
                created_at TIMESTAMPTZ
                DEFAULT CURRENT_TIMESTAMP
            )
        """)

    logger.info(
        "База данных готова"
    )


# ==================================================
# ФУНКЦИИ ПОДПИСОК
# ==================================================


async def get_subscription(
    user_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                subscription_end_date,
                is_active
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
                subscription_end_date,
                is_active
            )
            VALUES($1,$2,TRUE)

            ON CONFLICT(user_id)
            DO UPDATE SET
                subscription_end_date=$2,
                is_active=TRUE
            """,
            user_id,
            end_date
        )


async def deactivate_subscription(
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


async def delete_subscription(
    user_id: int
):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM users
            WHERE user_id=$1
            """,
            user_id
        )


# ==================================================
# ФУНКЦИИ ПЛАТЕЖЕЙ
# ==================================================


async def payment_exists(
    payment_id: str
) -> bool:

    async with db_pool.acquire() as conn:

        return await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM payments
                WHERE payment_id=$1
            )
            """,
            payment_id
        )


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
            VALUES($1,$2,$3,$4)
            """,
            payment_id,
            user_id,
            amount,
            payload
        )


# ==================================================
# СТАТИСТИКА БД
# ==================================================


async def get_stats():

    async with db_pool.acquire() as conn:

        users = await conn.fetchval(
            "SELECT COUNT(*) FROM users"
        )

        active = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE is_active=TRUE
            """
        )

        payments = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM payments
            """
        )

        return {
            "users": users,
            "active": active,
            "payments": payments
        }


# ==================================================
# ПРОВЕРКА АДМИНА
# ==================================================


def is_admin(
    user_id: int
) -> bool:

    return user_id == ADMIN_ID


async def admin_only(
    message: types.Message
):

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "⛔ Доступ запрещён"
        )

        return False

    return True

# ==================================================
# /START
# ==================================================


@dp.message(Command("start"))
async def cmd_start(message: types.Message):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Купить подписку",
                    callback_data="buy_subscription"
                )
            ]
        ]
    )

    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Этот бот выдаёт доступ к закрытому каналу.\n\n"
        f"⭐ Стоимость подписки: {PRICE_STARS} Stars\n"
        f"📅 Срок подписки: {SUBSCRIPTION_DAYS} дней\n\n"
        "📱 Для оплаты используйте приложение Telegram "
        "на телефоне. В некоторых веб-версиях Telegram "
        "оплата Stars может не открываться.",
        reply_markup=keyboard
    )

@dp.message(Command("status"))
async def bot_status(message: types.Message):

    if not await admin_only(message):
        return

    now = datetime.now().astimezone()

    uptime = now - BOT_START_TIME

    last_ping_text = (
        str(LAST_PING)
        if LAST_PING
        else "ещё не было"
    )

    await message.answer(
        "📊 Статус системы\n\n"
        f"⏱ Аптайм: {uptime}\n"
        f"📡 Последний ping: {last_ping_text}\n"
        f"🕒 Сейчас: {now}"
    )

# ==================================================
# КНОПКА ПОКУПКИ
# ==================================================


@dp.callback_query(F.data == "buy_subscription")
async def buy_subscription(
    callback: types.CallbackQuery
):

    await callback.answer(
        "Создаём счёт на оплату..."
    )

    try:

        payload = (
            f"subscription_"
            f"{callback.from_user.id}_"
            f"{int(datetime.now().timestamp())}"
        )

        prices = [
            LabeledPrice(
                label=(
                    f"Доступ на "
                    f"{SUBSCRIPTION_DAYS} дней"
                ),
                amount=PRICE_STARS
            )
        ]

        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="⭐ Подписка на канал",
            description=(
                f"Доступ к закрытому каналу "
                f"на {SUBSCRIPTION_DAYS} дней"
            ),
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="subscription"
        )

    except Exception:

        logger.exception(
            "Ошибка создания счёта"
        )

        await callback.message.answer(
            "❌ Не удалось открыть оплату.\n\n"
            "Попробуйте использовать Telegram "
            "на телефоне."
        )


# ==================================================
# ПОДТВЕРЖДЕНИЕ ОПЛАТЫ
# ==================================================


@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    await bot.answer_pre_checkout_query(
        query.id,
        ok=True
    )


# ==================================================
# УСПЕШНАЯ ОПЛАТА
# ==================================================


@dp.message(F.successful_payment)
async def successful_payment(
    message: types.Message
):

    payment = message.successful_payment

    payment_id = (
        payment.telegram_payment_charge_id
    )

    user_id = message.from_user.id


    # Защита от повторного получения обновления
    if await payment_exists(payment_id):

        await message.answer(
            "⚠️ Этот платёж уже обработан."
        )

        return


    # Сохраняем платёж
    await save_payment(
        payment_id,
        user_id,
        payment.total_amount,
        payment.invoice_payload
    )


    now = datetime.now().astimezone()


    # Проверяем существующую подписку
    current = await get_subscription(
        user_id
    )


    if (
        current
        and current["is_active"]
        and current["subscription_end_date"]
        and current["subscription_end_date"] > now
    ):

        # Продление активной подписки
        new_end_date = (
            current["subscription_end_date"]
            + timedelta(
                days=SUBSCRIPTION_DAYS
            )
        )

    else:

        # Новая подписка
        new_end_date = (
            now
            + timedelta(
                days=SUBSCRIPTION_DAYS
            )
        )


    # Сохраняем срок
    await set_subscription(
        user_id,
        new_end_date
    )


    # Создание персональной ссылки
    try:

        invite = (
            await bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                expire_date=new_end_date
            )
        )


        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📢 Войти в канал",
                        url=invite.invite_link
                    )
                ]
            ]
        )


        await message.answer(
            "✅ Оплата прошла успешно!\n\n"
            f"📅 Ваша подписка активна до:\n"
            f"{new_end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            "👇 Нажмите кнопку ниже, "
            "чтобы перейти в канал:",
            reply_markup=keyboard
        )


        logger.info(
            f"Ссылка выдана пользователю "
            f"{user_id}"
        )


    except Exception:

        logger.exception(
            "Ошибка создания ссылки"
        )


        await message.answer(
            "❌ Оплата получена, но не удалось "
            "выдать ссылку на канал.\n\n"
            "Свяжитесь с администратором."
        )


# ==================================================
# ДОПОЛНИТЕЛЬНО:
# команда пользователя для проверки статуса
# ==================================================


@dp.message(Command("status"))
async def my_status(
    message: types.Message
):

    data = await get_subscription(
        message.from_user.id
    )


    if (
        not data
        or not data["is_active"]
    ):
        await message.answer(
            "❌ У вас нет активной подписки.\n\n"
            "Используйте /start для покупки."
        )
        return


    end_date = data[
        "subscription_end_date"
    ]


    await message.answer(
        "✅ Ваша подписка активна.\n\n"
        f"Действует до:\n"
        f"{end_date.strftime('%d.%m.%Y %H:%M')}"
    )

# ==================================================
# УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ИЗ КАНАЛА
# ==================================================


async def kick_from_channel(
    user_id: int
):

    try:

        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )


        # Telegram не позволяет банить владельца и админов
        if member.status in (
            "creator",
            "administrator"
        ):

            logger.warning(
                f"Пользователь {user_id} является администратором канала. "
                "Удаление пропущено."
            )

            return


        # Бан + разбан = удаление из канала
        await bot.ban_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )


        await bot.unban_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )


        logger.info(
            f"Пользователь {user_id} удалён из канала"
        )


    except Exception:

        logger.exception(
            f"Ошибка удаления пользователя {user_id}"
        )


# ==================================================
# ПРОВЕРКА ПРОСРОЧЕННЫХ ПОДПИСОК
# Запускается автоматически каждый час
# ==================================================


async def check_expired_subscriptions():

    logger.info(
        "Проверка просроченных подписок..."
    )


    async with db_pool.acquire() as conn:

        expired_users = await conn.fetch(
            """
            SELECT user_id
            FROM users
            WHERE
                is_active = TRUE
                AND subscription_end_date < NOW()
            """
        )


    for row in expired_users:

        user_id = row["user_id"]


        await kick_from_channel(
            user_id
        )


        await deactivate_subscription(
            user_id
        )


        try:

            await bot.send_message(
                user_id,
                "⛔ Ваша подписка закончилась.\n\n"
                "Доступ к каналу закрыт.\n"
                "Используйте /start для продления."
            )


        except Exception:

            logger.warning(
                f"Не удалось отправить уведомление "
                f"пользователю {user_id}"
            )


# ==================================================
# НАПОМИНАНИЯ О ЗАКАНЧИВАЮЩЕЙСЯ ПОДПИСКЕ
# Запускается каждый день
# ==================================================


async def send_reminders():

    logger.info(
        "Проверка напоминаний..."
    )


    now = datetime.now().astimezone()


    async with db_pool.acquire() as conn:

        users = await conn.fetch(
            """
            SELECT
                user_id,
                subscription_end_date
            FROM users
            WHERE is_active = TRUE
            """
        )


    for user in users:

        user_id = user["user_id"]


        end_date = user[
            "subscription_end_date"
        ].astimezone()


        days_left = (
            end_date - now
        ).days


        try:

            if days_left == 3:

                await bot.send_message(
                    user_id,
                    "🔔 Напоминание!\n\n"
                    "До окончания вашей подписки "
                    "осталось 3 дня.\n\n"
                    "Используйте /start, "
                    "чтобы продлить доступ."
                )


            elif days_left == 1:

                await bot.send_message(
                    user_id,
                    "⚠️ Ваша подписка закончится "
                    "завтра.\n\n"
                    "Продлите её заранее через /start."
                )


        except Exception:

            logger.warning(
                f"Не удалось отправить "
                f"напоминание пользователю {user_id}"
            )


# ==================================================
# ВСПОМОГАТЕЛЬНАЯ КОМАНДА
# Проверка доступа пользователя к каналу
# ==================================================


@dp.message(Command("channel"))
async def check_channel_access(
    message: types.Message
):

    try:

        chat = await bot.get_chat(
            CHANNEL_ID
        )


        await message.answer(
            "✅ Канал доступен боту\n\n"
            f"Название: {chat.title}\n"
            f"ID: {chat.id}"
        )


    except Exception as e:

        await message.answer(
            "❌ Ошибка доступа к каналу:\n\n"
            f"{e}"
        )

# ==================================================
# АДМИН ПАНЕЛЬ
# ==================================================


@dp.message(Command("admin"))
async def admin_panel(message: types.Message):

    if not await admin_only(message):
        return

    await message.answer(
        "👑 Панель администратора\n\n"
        "/stats — статистика\n"
        "/balance — баланс Stars\n"
        "/check ID — проверить подписку\n"
        "/add ID дни — выдать подписку\n"
        "/extend ID дни — продлить подписку\n"
        "/remove ID — удалить подписку"
    )


# ==================================================
# СТАТИСТИКА
# ==================================================


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):

    if not await admin_only(message):
        return


    data = await get_stats()


    await message.answer(
        "📊 Статистика бота\n\n"
        f"👥 Всего пользователей: {data['users']}\n"
        f"✅ Активных подписок: {data['active']}\n"
        f"💳 Всего оплат: {data['payments']}"
    )


# ==================================================
# БАЛАНС TELEGRAM STARS
# ==================================================


@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):

    if not await admin_only(message):
        return


    try:

        balance = await bot.get_my_star_balance()


        await message.answer(
            "⭐ Баланс Telegram Stars\n\n"
            f"Доступно Stars: {balance.amount}"
        )


    except Exception:

        logger.exception(
            "Ошибка получения баланса Stars"
        )


        await message.answer(
            "❌ Не удалось получить баланс Stars."
        )


# ==================================================
# ПРОВЕРКА ПОДПИСКИ
# ==================================================


@dp.message(Command("check"))
async def cmd_check(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    if not command.args:

        await message.answer(
            "Использование:\n/check ID"
        )

        return


    try:

        user_id = int(command.args)


    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return


    data = await get_subscription(user_id)


    if not data:

        await message.answer(
            "Пользователь не найден."
        )

        return


    await message.answer(
        "Информация о пользователе:\n\n"
        f"ID: {user_id}\n"
        f"Активна: {'Да' if data['is_active'] else 'Нет'}\n"
        f"До: {data['subscription_end_date']}"
    )


# ==================================================
# ДОБАВЛЕНИЕ ПОДПИСКИ
# ==================================================


@dp.message(Command("add"))
async def cmd_add(
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
            "Использование:\n/add ID дни"
        )

        return


    end_date = (
        datetime.now().astimezone()
        + timedelta(days=days)
    )


    await set_subscription(
        user_id,
        end_date
    )


    await message.answer(
        f"✅ Подписка выдана до "
        f"{end_date.strftime('%d.%m.%Y')}"
    )


# ==================================================
# ПРОДЛЕНИЕ ПОДПИСКИ
# ==================================================


@dp.message(Command("extend"))
async def cmd_extend(
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
            "Использование:\n/extend ID дни"
        )

        return


    current = await get_subscription(user_id)


    now = datetime.now().astimezone()


    if (
        current
        and current["is_active"]
        and current["subscription_end_date"] > now
    ):

        new_date = (
            current["subscription_end_date"]
            + timedelta(days=days)
        )


    else:

        new_date = (
            now
            + timedelta(days=days)
        )


    await set_subscription(
        user_id,
        new_date
    )


    await message.answer(
        f"✅ Подписка продлена до "
        f"{new_date.strftime('%d.%m.%Y')}"
    )


# ==================================================
# УДАЛЕНИЕ ПОДПИСКИ
# ==================================================


@dp.message(Command("remove"))
async def cmd_remove(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    if not command.args:

        await message.answer(
            "Использование:\n/remove ID"
        )

        return


    try:

        user_id = int(command.args)


    except ValueError:

        await message.answer(
            "ID должен быть числом."
        )

        return


    await kick_from_channel(
        user_id
    )


    await delete_subscription(
        user_id
    )


    await message.answer(
        f"✅ Пользователь {user_id} удалён."
    )

# ==================================================
# HTTP СЕРВЕР ДЛЯ RENDER
# ==================================================


class HealthHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):

        global LAST_PING

        if self.path in ["/", "/health"]:

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

            LAST_PING = datetime.now().astimezone()

            logger.info("Health check OK")

        elif self.path == "/ping":

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"pong")

            LAST_PING = datetime.now().astimezone()

            logger.info("Cron ping received")

        else:

            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


# ==================================================
# ЗАПУСК БОТА
# ==================================================


async def main():

    logger.info(
        "Запуск бота..."
    )


    # Создание PostgreSQL пула
    await init_db()


    # Запускаем HTTP сервер Render
    threading.Thread(
        target=run_http_server,
        daemon=True
    ).start()


    # Планировщик задач
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


    scheduler.start()


    logger.info(
        "Планировщик запущен"
    )


    # Удаляем старый webhook
    # чтобы не было конфликта
    await bot.delete_webhook(
        drop_pending_updates=True
    )


    try:

        await dp.start_polling(
            bot
        )


    finally:

        logger.info(
            "Остановка бота..."
        )


        # Закрываем PostgreSQL pool
        if db_pool:

            await db_pool.close()

            logger.info(
                "PostgreSQL пул закрыт"
            )


        # Закрываем сессию Telegram
        await bot.session.close()

        logger.info(
            "Бот остановлен"
        )


# ==================================================
# ТОЧКА ВХОДА
# ==================================================


if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Бот остановлен вручную"
        )
