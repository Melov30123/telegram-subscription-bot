import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    LabeledPrice, PreCheckoutQuery, SuccessfulPayment,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatInviteLink, ChatMemberUpdated, ChatMember
)
from aiogram.utils.deep_linking import create_start_link
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Импорт для работы с базой данных
import asyncpg
from dotenv import load_dotenv
import os

load_dotenv()

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # ID вашего канала (например, -1001234567890)
DB_DSN = os.getenv("DATABASE_URL")   # Строка подключения к PostgreSQL

SUBSCRIPTION_PRICE_STARS = int(os.getenv("PRICE_STARS", 15))
SUBSCRIPTION_DAYS = 30                # Период подписки в днях

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Инициализация базы данных ---
async def init_db():
    """Создаёт необходимые таблицы, если они не существуют."""
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            subscription_end_date TIMESTAMPTZ,
            is_active BOOLEAN DEFAULT TRUE
        );
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS successful_payments (
            payment_id TEXT PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            payload TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    await conn.close()
    logger.info("Database initialized.")

# --- Работа с базой данных ---
async def set_user_subscription(user_id: int, end_date: datetime):
    """Устанавливает дату окончания подписки для пользователя."""
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('''
        INSERT INTO users (user_id, subscription_end_date, is_active)
        VALUES ($1, $2, TRUE)
        ON CONFLICT (user_id) DO UPDATE
        SET subscription_end_date = $2, is_active = TRUE
    ''', user_id, end_date)
    await conn.close()

async def get_user_subscription(user_id: int) -> Optional[datetime]:
    """Возвращает дату окончания подписки пользователя."""
    conn = await asyncpg.connect(DB_DSN)
    row = await conn.fetchrow('SELECT subscription_end_date FROM users WHERE user_id = $1', user_id)
    await conn.close()
    return row['subscription_end_date'] if row else None

async def deactivate_subscription(user_id: int):
    """Деактивирует подписку пользователя."""
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('UPDATE users SET is_active = FALSE WHERE user_id = $1', user_id)
    await conn.close()

async def add_successful_payment(payment_id: str, user_id: int, amount: int, payload: str):
    """Логирует успешный платёж."""
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('''
        INSERT INTO successful_payments (payment_id, user_id, amount, payload)
        VALUES ($1, $2, $3, $4)
    ''', payment_id, user_id, amount, payload)
    await conn.close()

# --- Фоновые задачи ---
scheduler = AsyncIOScheduler()

async def check_expired_subscriptions():
    """Проверяет истёкшие подписки и удаляет пользователей из канала."""
    logger.info("Checking for expired subscriptions...")
    conn = await asyncpg.connect(DB_DSN)
    rows = await conn.fetch('SELECT user_id FROM users WHERE subscription_end_date < NOW() AND is_active = TRUE')
    await conn.close()

    for row in rows:
        user_id = row['user_id']
        try:
            # Блокируем (кикаем) пользователя из канала
            await bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            # Сразу разблокируем, чтобы он мог зайти по новой ссылке, если оплатит снова
            await bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            await deactivate_subscription(user_id)
            logger.info(f"User {user_id} was removed from the channel due to subscription expiration.")
        except Exception as e:
            logger.error(f"Failed to remove user {user_id}: {e}")

async def send_reminders():
    """Отправляет напоминания об истечении подписки за 3 и 1 день."""
    conn = await asyncpg.connect(DB_DSN)
    rows = await conn.fetch('SELECT user_id, subscription_end_date FROM users WHERE is_active = TRUE')
    await conn.close()

    now = datetime.now().astimezone()
    for row in rows:
        user_id = row['user_id']
        end_date = row['subscription_end_date'].astimezone()
        days_left = (end_date - now).days

        if days_left == 3:
            await bot.send_message(
                user_id,
                f"🔔 *Ваша подписка истекает через 3 дня!*\n\n"
                f"Пожалуйста, продлите доступ, чтобы не потерять его.\n"
                f"Используйте команду /subscribe, чтобы оформить новую подписку.",
                parse_mode="Markdown"
            )
        elif days_left == 1:
            await bot.send_message(
                user_id,
                f"⚠️ *Ваша подписка истекает ЗАВТРА!*\n\n"
                f"Доступ будет автоматически заблокирован.\n"
                f"Пожалуйста, продлите его с помощью команды /subscribe.",
                parse_mode="Markdown"
            )

# --- Хендлеры ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обрабатывает команду /start, показывая главное меню."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оформить подписку", callback_data="subscribe")]
    ])
    await message.answer(
        "Добро пожаловать! 👋\n\n"
        "Этот бот предоставляет доступ к эксклюзивному контенту.\n"
        f"*Стоимость подписки:* {SUBSCRIPTION_PRICE_STARS} Telegram Stars в месяц.\n\n"
        "Нажмите на кнопку ниже, чтобы оформить подписку.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "subscribe")
async def process_subscribe(callback: types.CallbackQuery):
    """Обрабатывает нажатие на кнопку 'Оформить подписку'."""
    await callback.answer()
    user_id = callback.from_user.id

    # Проверяем, есть ли уже активная подписка
    end_date = await get_user_subscription(user_id)
    if end_date and end_date > datetime.now().astimezone():
        await callback.message.answer(
            f"✅ У вас уже есть активная подписка до *{end_date.strftime('%d.%m.%Y')}*.\n"
            "Продлить её можно будет после истечения срока или с помощью специальной команды.",
            parse_mode="Markdown"
        )
        return

    # Готовим счёт на оплату
    prices = [LabeledPrice(label="1 месяц доступа к каналу", amount=SUBSCRIPTION_PRICE_STARS)]
    payload = f"sub_{user_id}_{int(datetime.now().timestamp())}"

    await bot.send_invoice(
        chat_id=user_id,
        title="⭐ Оплата подписки на канал",
        description=f"Доступ к закрытому каналу на {SUBSCRIPTION_DAYS} дней.",
        payload=payload,
        provider_token="",  # Для Stars оставляем пустым
        currency="XTR",     # Валюта Telegram Stars
        prices=prices,
        start_parameter="subscription"
    )

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Подтверждает возможность оплаты."""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    """Обрабатывает успешную оплату."""
    payment = message.successful_payment
    user_id = message.from_user.id

    # Разбираем payload для верификации (опционально)
    payload_parts = payment.invoice_payload.split('_')
    if payload_parts[0] != "sub":
        logger.error(f"Unknown payload format: {payment.invoice_payload}")
        return

    # Записываем платёж в базу
    await add_successful_payment(payment.telegram_payment_charge_id, user_id, payment.total_amount, payment.invoice_payload)

    # Устанавливаем дату окончания подписки
    new_end_date = datetime.now().astimezone() + timedelta(days=SUBSCRIPTION_DAYS)
    await set_user_subscription(user_id, new_end_date)

    # Создаём персональную ссылку на канал
    invite_link = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        member_limit=1,
        expire_date=new_end_date
    )

    # Отправляем пользователю ссылку на канал
    await message.answer(
        f"✅ *Оплата прошла успешно!*\n\n"
        f"Ваша подписка активна до *{new_end_date.strftime('%d.%m.%Y')}*.\n\n"
        f"Вот ваша персональная ссылка для вступления в канал:\n{invite_link.invite_link}\n\n"
        f"Доступ будет автоматически продлён после следующей оплаты. "
        f"Если у вас возникли вопросы, напишите администратору.",
        parse_mode="Markdown"
    )

    # Также можно добавить кнопку для перехода в канал
    await message.answer(
        "🔗 *Нажмите на кнопку ниже, чтобы присоединиться к каналу:*",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Перейти в канал", url=invite_link.invite_link)]
        ]),
        parse_mode="Markdown"
    )

# --- Команда для администратора (продление/отмена) ---
@dp.message(Command("extend"))
async def cmd_extend(message: types.Message, command: CommandObject):
    """Команда администратора для продления подписки пользователя (опционально)."""
    if message.from_user.id != int(os.getenv("ADMIN_ID")):
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return

    args = command.args
    if not args:
        await message.answer("❌ Использование: /extend <user_id> <days>")
        return

    parts = args.split()
    if len(parts) != 2:
        await message.answer("❌ Использование: /extend <user_id> <days>")
        return

    try:
        user_id = int(parts[0])
        days = int(parts[1])
    except ValueError:
        await message.answer("❌ Аргументы должны быть числами.")
        return

    current_end_date = await get_user_subscription(user_id)
    if current_end_date and current_end_date > datetime.now().astimezone():
        new_end_date = current_end_date + timedelta(days=days)
    else:
        new_end_date = datetime.now().astimezone() + timedelta(days=days)

    await set_user_subscription(user_id, new_end_date)
    await message.answer(f"✅ Подписка пользователя {user_id} продлена до {new_end_date.strftime('%d.%m.%Y')}.")

# --- Запуск планировщика ---
async def on_startup():
    await init_db()
    # Запускаем проверку истекших подписок раз в час
    scheduler.add_job(check_expired_subscriptions, 'interval', hours=1)
    # Запускаем отправку напоминаний раз в день (например, в 10:00 утра)
    scheduler.add_job(send_reminders, CronTrigger(hour=10, minute=0))
    scheduler.start()
    logger.info("Scheduler started.")

async def on_shutdown():
    scheduler.shutdown()
    await bot.session.close()
    logger.info("Bot stopped.")

# --- Точка входа ---
async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
