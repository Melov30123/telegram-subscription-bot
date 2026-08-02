from __future__ import annotations

import asyncio
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from subscription_bot.config import Settings
from subscription_bot.database import Database
from subscription_bot.locales import tr
from subscription_bot.services import AccessService

logger = logging.getLogger(__name__)


async def remove_expired(database: Database, access_service: AccessService) -> None:
    user_ids = await database.expired_users_pending_removal()
    removed = 0
    for user_id in user_ids:
        if await access_service.remove_from_channel(user_id):
            await database.mark_access_removed(user_id)
            removed += 1
        await asyncio.sleep(0.04)
    if user_ids:
        logger.info("Expired subscriptions processed: %s/%s", removed, len(user_ids))


async def send_reminders(bot: Bot, database: Database) -> None:
    for days_before in (7, 3, 1):
        rows = await database.due_reminders(days_before)
        for row in rows:
            try:
                await bot.send_message(
                    row["telegram_id"],
                    tr(row["language_code"], "reminder", days=days_before),
                )
                await database.mark_reminder_delivered(
                    row["telegram_id"], row["access_until"], days_before
                )
            except TelegramForbiddenError:
                await database.mark_bot_blocked(row["telegram_id"])
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 0.2)
            except TelegramBadRequest:
                logger.info("Reminder rejected for user %s", row["telegram_id"])
            await asyncio.sleep(0.04)


def create_scheduler(
    bot: Bot, database: Database, access_service: AccessService, settings: Settings
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(
        remove_expired,
        "interval",
        minutes=10,
        args=(database, access_service),
        id="remove_expired",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        send_reminders,
        "interval",
        hours=1,
        args=(bot, database),
        id="send_reminders",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        database.cleanup,
        "cron",
        hour=4,
        minute=15,
        id="database_cleanup",
        coalesce=True,
        max_instances=1,
    )
    return scheduler
