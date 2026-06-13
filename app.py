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


# =====================================================
# ЗАГРУЗКА ENV
# =====================================================

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Переменная окружения {name} не найдена!"
        )

    return value


BOT_TOKEN = require_env("BOT_TOKEN")
CHANNEL_ID = int(require_env("CHANNEL_ID"))
DATABASE_URL = require_env("DATABASE_URL")

# Администратор бота
ADMIN_ID = int(require_env("ADMIN_ID"))

# Стоимость в Telegram Stars
PRICE_STARS = int(
    os.getenv("PRICE_STARS", "15")
)

SUBSCRIPTION_DAYS = 30


# =====================================================
# ЛОГИ
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =====================================================
# BOT / DISPATCHER / SCHEDULER
# =====================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()

scheduler = AsyncIOScheduler()


# =====================================================
# БАЗА ДАННЫХ
# =====================================================


async def init_db():
    """
    Создание таблиц.
    """

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        # Пользователи
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                subscription_end_date TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE
            )
        """)

        # Оплаты
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

    finally:
        await conn.close()


# =====================================================
# ФУНКЦИИ ПОЛЬЗОВАТЕЛЕЙ
# =====================================================


async def get_subscription(
    user_id: int
) -> Optional[datetime]:

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        row = await conn.fetchrow(
            """
            SELECT subscription_end_date,
                   is_active
            FROM users
            WHERE user_id=$1
            """,
            user_id
        )

        return row

    finally:
        await conn.close()


async def set_subscription(
    user_id: int,
    end_date: datetime
):

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        await conn.execute(
            """
            INSERT INTO users (
                user_id,
                subscription_end_date,
                is_active
            )
            VALUES ($1, $2, TRUE)

            ON CONFLICT (user_id)
            DO UPDATE SET
            subscription_end_date=$2,
            is_active=TRUE
            """,
            user_id,
            end_date
        )

    finally:
        await conn.close()


async def deactivate_subscription(
    user_id: int
):

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        await conn.execute(
            """
            UPDATE users
            SET is_active=FALSE
            WHERE user_id=$1
            """,
            user_id
        )

    finally:
        await conn.close()


async def delete_subscription(
    user_id: int
):

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        await conn.execute(
            """
            DELETE FROM users
            WHERE user_id=$1
            """,
            user_id
        )

    finally:
        await conn.close()


# =====================================================
# ОПЛАТЫ
# =====================================================


async def payment_exists(
    payment_id: str
) -> bool:

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        result = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM payments
                WHERE payment_id=$1
            )
            """,
            payment_id
        )

        return result

    finally:
        await conn.close()


async def save_payment(
    payment_id: str,
    user_id: int,
    amount: int,
    payload: str
):

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

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

    finally:
        await conn.close()


# =====================================================
# ПРОВЕРКА АДМИНА
# =====================================================


def is_admin(
    user_id: int
) -> bool:

    return user_id == ADMIN_ID


async def admin_only(
    message: types.Message
) -> bool:

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "⛔ У вас нет доступа."
        )

        return False

    return True

# =====================================================
# ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ
# =====================================================


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
        "Этот бот выдаёт доступ в закрытый Telegram канал.\n\n"
        f"⭐ Стоимость: {PRICE_STARS} Stars\n"
        f"📅 Срок доступа: {SUBSCRIPTION_DAYS} дней",
        reply_markup=keyboard
    )


# =====================================================
# ПОКУПКА ПОДПИСКИ
# =====================================================


@dp.callback_query(F.data == "buy_subscription")
async def buy_subscription(callback: types.CallbackQuery):

    await callback.answer()

    payload = (
        f"sub_"
        f"{callback.from_user.id}_"
        f"{int(datetime.now().timestamp())}"
    )

    prices = [
        LabeledPrice(
            label="Доступ в закрытый канал на 30 дней",
            amount=PRICE_STARS
        )
    ]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="⭐ Подписка на канал",
        description=(
            f"Доступ на {SUBSCRIPTION_DAYS} дней"
        ),
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="subscription"
    )


# =====================================================
# ПОДТВЕРЖДЕНИЕ ОПЛАТЫ
# =====================================================


@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    await bot.answer_pre_checkout_query(
        query.id,
        ok=True
    )


# =====================================================
# УСПЕШНАЯ ОПЛАТА
# =====================================================


@dp.message(F.successful_payment)
async def successful_payment(
    message: types.Message
):

    payment = message.successful_payment

    payment_id = (
        payment.telegram_payment_charge_id
    )

    user_id = message.from_user.id


    # Защита от повторной обработки
    if await payment_exists(payment_id):

        await message.answer(
            "⚠️ Платёж уже был обработан."
        )
        return


    await save_payment(
        payment_id,
        user_id,
        payment.total_amount,
        payment.invoice_payload
    )


    # Проверяем старую подписку
    current = await get_subscription(
        user_id
    )


    now = datetime.now().astimezone()


    if (
        current
        and current["is_active"]
        and current["subscription_end_date"]
        and current["subscription_end_date"] > now
    ):

        # Продлеваем текущую
        end_date = (
            current["subscription_end_date"]
            + timedelta(days=SUBSCRIPTION_DAYS)
        )

    else:

        # Новая подписка
        end_date = (
            now
            + timedelta(days=SUBSCRIPTION_DAYS)
        )


    await set_subscription(
        user_id,
        end_date
    )


    # Создаём ссылку в канал
    try:

        invite = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=end_date
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
            "✅ Оплата получена!\n\n"
            f"📅 Подписка активна до "
            f"{end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            "Нажмите кнопку ниже, чтобы войти в канал 👇",
            reply_markup=keyboard
        )


        logger.info(
            f"Выдана ссылка пользователю {user_id}"
        )


    except Exception:

        logger.exception(
            "Ошибка создания ссылки"
        )

        await message.answer(
            "❌ Оплата прошла, но произошла ошибка "
            "при выдаче ссылки.\n"
            "Свяжитесь с администратором."
        )


# =====================================================
# НАПОМИНАНИЯ О ЗАКОНЧАНИИ ПОДПИСКИ
# =====================================================


async def send_reminders():

    conn = await asyncpg.connect(
        DATABASE_URL
    )


    try:

        users = await conn.fetch(
            """
            SELECT user_id,
                   subscription_end_date
            FROM users
            WHERE is_active = TRUE
            """
        )


    finally:

        await conn.close()


    now = datetime.now().astimezone()


    for user in users:

        try:

            end = user["subscription_end_date"]


            if not end:
                continue


            days_left = (
                end - now
            ).days


            if days_left == 3:

                await bot.send_message(
                    user["user_id"],
                    "🔔 Напоминаем!\n\n"
                    "До окончания вашей подписки "
                    "осталось 3 дня.\n\n"
                    "Используйте /start для продления."
                )


            elif days_left == 1:

                await bot.send_message(
                    user["user_id"],
                    "⚠️ Внимание!\n\n"
                    "Завтра закончится доступ "
                    "к вашему каналу.\n\n"
                    "Продлите подписку заранее."
                )


        except Exception:

            logger.exception(
                f"Ошибка отправки напоминания "
                f"пользователю {user['user_id']}"
            )


# =====================================================
# УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЕЙ С ИСТЁКШЕЙ ПОДПИСКОЙ
# =====================================================


async def kick_from_channel(user_id: int):
    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )

        if member.status in [
            "creator",
            "administrator"
        ]:
            logger.warning(
                f"Пользователь {user_id} является администратором канала. Удаление пропущено."
            )
            return

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


async def check_expired_subscriptions():

    logger.info(
        "Проверка просроченных подписок..."
    )

    conn = await asyncpg.connect(
        DATABASE_URL
    )

    try:

        users = await conn.fetch(
            """
            SELECT user_id
            FROM users
            WHERE is_active = TRUE
            AND subscription_end_date < NOW()
            """
        )

    finally:

        await conn.close()


    for user in users:

        user_id = user["user_id"]

        await kick_from_channel(user_id)

        await deactivate_subscription(
            user_id
        )


# =====================================================
# АДМИН ПАНЕЛЬ
# =====================================================


@dp.message(Command("admin"))
async def admin_panel(message: types.Message):

    if not await admin_only(message):
        return


    await message.answer(
        """
🛠 Админ панель

/add ID ДНИ
Добавить подписку

/extend ID ДНИ
Продлить подписку

/remove ID
Удалить подписку

/check ID
Проверить пользователя

/stats
Статистика
"""
    )


# -----------------------------------------------------


@dp.message(Command("add"))
async def admin_add(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    if not command.args:
        await message.answer(
            "Пример:\n/add 123456789 30"
        )
        return


    try:

        user_id, days = map(
            int,
            command.args.split()
        )

    except ValueError:

        await message.answer(
            "Неверные аргументы"
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
        f"✅ Пользователь {user_id}\n"
        f"получил доступ до "
        f"{end_date.strftime('%d.%m.%Y')}"
    )


# -----------------------------------------------------


@dp.message(Command("extend"))
async def admin_extend(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    if not command.args:
        await message.answer(
            "Пример:\n/extend 123456789 30"
        )
        return


    try:

        user_id, days = map(
            int,
            command.args.split()
        )

    except ValueError:

        await message.answer(
            "Неверные аргументы"
        )

        return


    current = await get_subscription(
        user_id
    )


    now = datetime.now().astimezone()


    if (
        current
        and current["is_active"]
        and current["subscription_end_date"]
        and current["subscription_end_date"] > now
    ):

        new_end = (
            current["subscription_end_date"]
            + timedelta(days=days)
        )

    else:

        new_end = (
            now
            + timedelta(days=days)
        )


    await set_subscription(
        user_id,
        new_end
    )


    await message.answer(
        f"✅ Продлено до "
        f"{new_end.strftime('%d.%m.%Y')}"
    )


# -----------------------------------------------------


@dp.message(Command("remove"))
async def admin_remove(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    if not command.args:

        await message.answer(
            "Пример:\n/remove ID"
        )

        return


    try:

        user_id = int(
            command.args
        )

    except ValueError:

        await message.answer(
            "ID должен быть числом"
        )

        return


    await kick_from_channel(
        user_id
    )


    await delete_subscription(
        user_id
    )


    await message.answer(
        "🗑 Подписка удалена"
    )


# -----------------------------------------------------


@dp.message(Command("check"))
async def admin_check(
    message: types.Message,
    command: CommandObject
):

    if not await admin_only(message):
        return


    if not command.args:

        await message.answer(
            "Пример:\n/check ID"
        )

        return


    user_id = int(
        command.args
    )


    data = await get_subscription(
        user_id
    )


    if not data:

        await message.answer(
            "Пользователь не найден"
        )

        return


    status = (
        "🟢 Активна"
        if data["is_active"]
        else "🔴 Не активна"
    )


    await message.answer(
        f"""
ID: {user_id}

Статус: {status}

До:
{data["subscription_end_date"]}
"""
    )


# =====================================================
# СТАТИСТИКА
# =====================================================


@dp.message(Command("stats"))
async def stats(
    message: types.Message
):

    if not await admin_only(message):
        return


    conn = await asyncpg.connect(
        DATABASE_URL
    )


    try:

        total_users = await conn.fetchval(
            "SELECT COUNT(*) FROM users"
        )


        active = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM users
            WHERE is_active = TRUE
            """
        )


        payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments"
        )


    finally:

        await conn.close()


    await message.answer(
        f"""
📊 Статистика

Всего пользователей:
{total_users}

Активных подписок:
{active}

Всего оплат:
{payments}
"""
    )


# =====================================================
# HTTP SERVER ДЛЯ RENDER
# =====================================================


class HealthHandler(
    http.server.SimpleHTTPRequestHandler
):

    def do_GET(self):

        if self.path in ["/", "/health"]:

            self.send_response(200)

            self.end_headers()

            self.wfile.write(
                b"OK"
            )

        else:

            self.send_response(404)

            self.end_headers()


def start_http_server():

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )


    with socketserver.TCPServer(
        ("0.0.0.0", port),
        HealthHandler
    ) as server:

        logger.info(
            f"HTTP сервер запущен на {port}"
        )

        server.serve_forever()


# =====================================================
# ЗАПУСК БОТА
# =====================================================


async def main():

    logger.info(
        "Запуск бота..."
    )


    await init_db()


    threading.Thread(
        target=start_http_server,
        daemon=True
    ).start()


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

    # Удаляем возможный старый webhook
    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
