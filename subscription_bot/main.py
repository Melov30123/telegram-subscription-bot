from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    ErrorEvent,
)

from subscription_bot.config import Settings, get_settings
from subscription_bot.database import Database
from subscription_bot.health import HealthServer
from subscription_bot.jobs import create_scheduler
from subscription_bot.middleware import UserTrackingMiddleware
from subscription_bot.routers.admin import create_admin_router
from subscription_bot.routers.payments import router as payments_router
from subscription_bot.routers.user import router as user_router
from subscription_bot.services import AccessService, BroadcastService

logger = logging.getLogger(__name__)

USER_COMMANDS = [
    BotCommand(command="start", description="Открыть главное меню"),
    BotCommand(command="plans", description="Тарифы и покупка"),
    BotCommand(command="guide", description="Купить или скачать гайд"),
    BotCommand(command="my", description="Моя подписка"),
    BotCommand(command="invite", description="Ссылка в канал"),
    BotCommand(command="promo", description="Активировать промокод"),
    BotCommand(command="language", description="Сменить язык"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="paysupport", description="Поддержка по оплате"),
    BotCommand(command="terms", description="Условия"),
]

ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="admin", description="Панель администратора"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="users", description="Пользователи и поиск"),
    BotCommand(command="user", description="Карточка пользователя"),
    BotCommand(command="grant", description="Выдать/продлить подписку"),
    BotCommand(command="revoke", description="Отозвать подписку"),
    BotCommand(command="payments", description="Последние платежи"),
    BotCommand(command="plans_admin", description="Управление тарифами"),
    BotCommand(command="promos", description="Управление промокодами"),
    BotCommand(command="broadcast", description="Новая рассылка"),
    BotCommand(command="export", description="Экспорт пользователей"),
    BotCommand(command="health", description="Состояние системы"),
    BotCommand(command="admin_help", description="Все команды админки"),
]


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def configure_bot_commands(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logger.warning("Could not set command menu for admin %s", admin_id)


async def check_channel(bot: Bot, settings: Settings) -> None:
    me = await bot.get_me()
    member = await bot.get_chat_member(settings.channel_id, me.id)
    if member.status not in {"administrator", "creator"}:
        logger.warning("Bot is not a channel administrator; invite and kick operations will fail")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings)
    await database.connect()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage)
    access_service = AccessService(bot, database, settings)
    broadcast_service = BroadcastService(bot, database, settings)
    health_server = HealthServer(database, settings.port)
    scheduler = create_scheduler(bot, database, access_service, settings)

    dispatcher.include_router(create_admin_router(settings))
    dispatcher.include_router(payments_router)
    dispatcher.include_router(user_router)
    dispatcher.message.outer_middleware(UserTrackingMiddleware())
    dispatcher.callback_query.outer_middleware(UserTrackingMiddleware())

    @dispatcher.errors()
    async def error_handler(event: ErrorEvent) -> bool:
        logger.error(
            "Unhandled update error: %s",
            event.exception,
            exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
        )
        return True

    try:
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
        await configure_bot_commands(bot, settings)
        try:
            await check_channel(bot, settings)
        except Exception:
            logger.exception("Channel startup check failed")
        await health_server.start()
        scheduler.start()
        await broadcast_service.resume_queued()
        logger.info("Telegram Subscription Bot 4.0.0 is ready")
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            database=database,
            settings=settings,
            access_service=access_service,
            broadcast_service=broadcast_service,
        )
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await broadcast_service.stop()
        await health_server.stop()
        await storage.close()
        await bot.session.close()
        await database.close()


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
