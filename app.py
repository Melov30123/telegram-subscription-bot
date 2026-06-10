import asyncio
import logging
import os
import threading
import http.server
import socketserver
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncpg
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
DB_DSN = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PRICE_STARS = int(os.getenv("PRICE_STARS", "15"))

SUBSCRIPTION_DAYS = 30

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- База данных (без изменений) ---
async def init_db():
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            subscription_end_date TIMESTAMPTZ,
            is_active BOOLEAN DEFAULT TRUE
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS successful_payments (
            payment_id TEXT PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            payload TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await conn.close()
    logger.info("Database initialized.")

async def set_user_subscription(user_id: int, end_date: datetime):
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('''
        INSERT INTO users (user_id, subscription_end_date, is_active)
        VALUES ($1, $2, TRUE)
        ON CONFLICT (user_id) DO UPDATE
        SET subscription_end_date = $2, is_active = TRUE
    ''', user_id, end_date)
    await conn.close()

async def get_user_subscription(user_id: int) -> Optional[datetime]:
    conn = await asyncpg.connect(DB_DSN)
    row = await conn.fetchrow('SELECT subscription_end_date FROM users WHERE user_id = $1', user_id)
    await conn.close()
    return row['subscription_end_date'] if row else None

async def deactivate_subscription(user_id: int):
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('UPDATE users SET is_active = FALSE WHERE user_id = $1', user_id)
    await conn.close()

async def add_successful_payment(payment_id: str, user_id: int, amount: int, payload: str):
    conn = await asyncpg.connect(DB_DSN)
    await conn.execute('''
        INSERT INTO successful_payments (payment_id, user_id, amount, payload)
        VALUES ($1, $2, $3, $4)
    ''', payment_id, user_id, amount, payload)
    await conn.close()

# --- Фоновые задачи ---
async def check_expired_subscriptions():
    logger.info("Checking expired subscriptions...")
    conn = await asyncpg.connect(DB_DSN)
    rows = await conn.fetch('SELECT user_id FROM users WHERE subscription_end_date < NOW() AND is_active = TRUE')
    await conn.close()
    for row in rows:
        user_id = row['user_id']
        try:
            await bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            await bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            await deactivate_subscription(user_id)
            logger.info(f"User {user_id} removed from channel.")
        except Exception as e:
            logger.error(f"Failed to remove user {user_id}: {e}")

async def send_reminders():
    conn = await asyncpg.connect(DB_DSN)
    rows = await conn.fetch('SELECT user_id, subscription_end_date FROM users WHERE is_active = TRUE')
    await conn.close()
    now = datetime.now().astimezone()
    for row in rows:
        user_id = row['user_id']
        end_date = row['subscription_end_date'].astimezone()
        days_left = (end_date - now).days
        if days_left == 3:
            await bot.send_message(user_id, "🔔 *Ваша подписка истекает через 3 дня!* Продлите её командой /subscribe.", parse_mode="Markdown")
        elif days_left == 1:
            await bot.send_message(user_id, "⚠️ *Ваша подписка истекает ЗАВТРА!* Доступ будет автоматически заблокирован.", parse_mode="Markdown")

# --- Простой HTTP-сервер в отдельном потоке для Render ---
class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)

def run_http_server():
    port = int(os.environ.get('PORT', 8080))
    with socketserver.TCPServer(("0.0.0.0", port), HealthHandler) as httpd:
        logger.info(f"HTTP server started on port {port}")
        httpd.serve_forever()

# --- Хендлеры бота ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Оформить подписку", callback_data="subscribe")]
    ])
    await message.answer(
        "Добро пожаловать! 👋\n\n"
        "Этот бот предоставляет доступ к закрытому каналу.\n"
        f"*Стоимость подписки:* {PRICE_STARS} Telegram Stars на {SUBSCRIPTION_DAYS} дней.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "subscribe")
async def process_subscribe(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    end_date = await get_user_subscription(user_id)
    if end_date and end_date > datetime.now().astimezone():
        await callback.message.answer(
            f"✅ У вас уже есть активная подписка до *{end_date.strftime('%d.%m.%Y')}*.",
            parse_mode="Markdown"
        )
        return
    prices = [LabeledPrice(label="1 месяц доступа", amount=PRICE_STARS)]
    payload = f"sub_{user_id}_{int(datetime.now().timestamp())}"
    await bot.send_invoice(
        chat_id=user_id,
        title="⭐ Оплата подписки на канал",
        description=f"Доступ к закрытому каналу на {SUBSCRIPTION_DAYS} дней.",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="subscription"
    )

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payment = message.successful_payment
    user_id = message.from_user.id
    await add_successful_payment(payment.telegram_payment_charge_id, user_id, payment.total_amount, payment.invoice_payload)
    new_end_date = datetime.now().astimezone() + timedelta(days=SUBSCRIPTION_DAYS)
    await set_user_subscription(user_id, new_end_date)
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            member_limit=1,
            expire_date=new_end_date
        )
        await message.answer(
            f"✅ *Оплата прошла успешно!*\n\n"
            f"Ваша подписка активна до *{new_end_date.strftime('%d.%m.%Y')}*.\n\n"
            f"Вот ваша персональная ссылка для вступления в канал:\n{invite_link.invite_link}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Перейти в канал", url=invite_link.invite_link)]
            ])
        )
    except Exception as e:
        logger.error(f"Failed to create invite link for user {user_id}: {e}")
        await message.answer("❌ Не удалось создать ссылку на канал. Сообщите администратору.")

@dp.message(Command("extend"))
async def cmd_extend(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет прав.")
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
    current_end = await get_user_subscription(user_id)
    if current_end and current_end > datetime.now().astimezone():
        new_end = current_end + timedelta(days=days)
    else:
        new_end = datetime.now().astimezone() + timedelta(days=days)
    await set_user_subscription(user_id, new_end)
    await message.answer(f"✅ Подписка пользователя {user_id} продлена до {new_end.strftime('%d.%m.%Y')}.")

# --- Основная функция ---
async def main():
    await init_db()
    # Запускаем HTTP-сервер в отдельном потоке
    threading.Thread(target=run_http_server, daemon=True).start()
    # Планировщик
    scheduler.add_job(check_expired_subscriptions, 'interval', hours=1)
    scheduler.add_job(send_reminders, CronTrigger(hour=10, minute=0))
    scheduler.start()
    logger.info("Scheduler started.")
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
