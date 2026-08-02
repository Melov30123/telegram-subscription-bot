from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from subscription_bot.database import Database

logger = logging.getLogger(__name__)


class UserTrackingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user if isinstance(event, (Message, CallbackQuery)) else None
        database: Database | None = data.get("database")
        if user is not None and database is not None and not user.is_bot:
            try:
                await database.upsert_user(
                    user.id, user.username, user.first_name, user.last_name, user.language_code
                )
            except Exception:
                logger.exception("Could not update Telegram user %s", user.id)
        return await handler(event, data)
