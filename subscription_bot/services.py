from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from subscription_bot.config import Settings
from subscription_bot.database import Database, utcnow

logger = logging.getLogger(__name__)


class AccessService:
    def __init__(self, bot: Bot, database: Database, settings: Settings) -> None:
        self.bot = bot
        self.database = database
        self.settings = settings

    async def create_invite(self, user_id: int, access_until) -> str:
        old_links = await self.database.active_invite_links(user_id)
        for link in old_links:
            try:
                await self.bot.revoke_chat_invite_link(self.settings.channel_id, link)
            except TelegramBadRequest:
                logger.info("Invite was already invalid: %s", link)
            finally:
                await self.database.mark_invite_revoked(link)

        expires_at = min(access_until, utcnow() + timedelta(hours=24))
        invite = await self.bot.create_chat_invite_link(
            chat_id=self.settings.channel_id,
            name=f"subscriber_{user_id}",
            expire_date=expires_at,
            member_limit=1,
        )
        await self.database.create_invite(user_id, invite.invite_link, expires_at)
        return invite.invite_link

    async def remove_from_channel(self, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(self.settings.channel_id, user_id)
            if member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
                logger.warning("Refusing to remove channel administrator %s", user_id)
                return False
            await self.bot.ban_chat_member(self.settings.channel_id, user_id)
            await self.bot.unban_chat_member(self.settings.channel_id, user_id, only_if_banned=True)
            return True
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if "user not found" in lowered or "participant_id_invalid" in lowered:
                return True
            logger.warning("Could not remove user %s: %s", user_id, exc)
            return False


class BroadcastService:
    def __init__(self, bot: Bot, database: Database, settings: Settings) -> None:
        self.bot = bot
        self.database = database
        self.settings = settings
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, broadcast_id: int) -> None:
        task = asyncio.create_task(self.run(broadcast_id), name=f"broadcast-{broadcast_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume_queued(self) -> None:
        for broadcast_id in await self.database.queued_broadcast_ids():
            self.start(broadcast_id)

    async def stop(self) -> None:
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def run(self, broadcast_id: int) -> None:
        broadcast = await self.database.claim_broadcast(broadcast_id)
        if broadcast is None:
            return
        sent = 0
        failed = 0
        try:
            recipients = await self.database.broadcast_recipients(broadcast["segment"])
            total = len(recipients)
            await self.database.update_broadcast_progress(broadcast_id, total, sent, failed)
            delay = 1 / self.settings.broadcast_rate_per_second
            for index, user_id in enumerate(recipients, start=1):
                try:
                    await self.bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=broadcast["source_chat_id"],
                        message_id=broadcast["source_message_id"],
                    )
                    sent += 1
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 0.2)
                    try:
                        await self.bot.copy_message(
                            chat_id=user_id,
                            from_chat_id=broadcast["source_chat_id"],
                            message_id=broadcast["source_message_id"],
                        )
                        sent += 1
                    except Exception:
                        failed += 1
                except TelegramForbiddenError:
                    failed += 1
                    await self.database.mark_bot_blocked(user_id)
                except TelegramBadRequest:
                    failed += 1
                if index % 100 == 0:
                    await self.database.update_broadcast_progress(
                        broadcast_id, total, sent, failed
                    )
                await asyncio.sleep(delay)
            await self.database.finish_broadcast(broadcast_id, sent, failed)
            try:
                await self.bot.send_message(
                    broadcast["created_by"],
                    f"✅ Рассылка #{broadcast_id} завершена.\n"
                    f"Доставлено: {sent}\nОшибок: {failed}",
                )
            except TelegramForbiddenError:
                pass
        except asyncio.CancelledError:
            await self.database.finish_broadcast(
                broadcast_id, sent, failed, "Application stopped during broadcast"
            )
            raise
        except Exception as exc:
            logger.exception("Broadcast %s failed", broadcast_id)
            await self.database.finish_broadcast(broadcast_id, sent, failed, str(exc)[:1000])
