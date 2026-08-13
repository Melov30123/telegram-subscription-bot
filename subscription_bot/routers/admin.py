from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime

import asyncpg
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from subscription_bot.config import Settings
from subscription_bot.database import Database
from subscription_bot.keyboards import (
    admin_back_keyboard,
    admin_keyboard,
    broadcast_confirm_keyboard,
    broadcast_segments_keyboard,
    users_keyboard,
)
from subscription_bot.services import AccessService, BroadcastService
from subscription_bot.states import AdminBroadcast
from subscription_bot.utils import format_datetime, parse_int, safe

logger = logging.getLogger(__name__)

ADMIN_HELP = """<b>Все команды администратора</b>

<b>Обзор</b>
/admin — интерактивная панель
/stats — пользователи, подписки и выручка
/health — Telegram и PostgreSQL
/settings — безопасный обзор настроек

<b>Пользователи и доступ</b>
/users [ID или username] — поиск
/user ID — карточка пользователя
/grant ID ДНИ [причина] — выдать/продлить доступ
/extend ID ДНИ [причина] — то же самое
/revoke ID [причина] — отозвать подписку
/kick ID — удалить из канала
/block ID — запретить покупки
/unblock ID — снять запрет
/notify ID ТЕКСТ — личное сообщение
/export — CSV со всеми пользователями

<b>Оплата и тарифы</b>
/guide — купить или повторно получить ссылку на гайд
/payments [КОЛИЧЕСТВО] — последние платежи
/refund TELEGRAM_CHARGE_ID — возврат Stars; для подписки также сокращает доступ
/plans_admin — список всех тарифов
/plan_add CODE STARS ДНИ НАЗВАНИЕ — создать тариф
/plan_toggle CODE — включить/выключить тариф

<b>Промокоды</b>
/promos — список кодов
/promo_add CODE ДНИ ЛИМИТ — создать; ЛИМИТ=0 без лимита
/promo_disable CODE — отключить

<b>Коммуникации</b>
/broadcast [all|active|expired] — рассылка текста или медиа
/cancel — отменить текущий диалог
/audit [КОЛИЧЕСТВО] — журнал действий
"""


class IsAdmin(Filter):
    def __init__(self, admin_ids: frozenset[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and event.from_user.id in self.admin_ids)


def parse_user_and_days(args: str | None) -> tuple[int, int, str]:
    if not args:
        raise ValueError("Укажите ID и число дней")
    parts = args.split(maxsplit=2)
    if len(parts) < 2:
        raise ValueError("Укажите ID и число дней")
    user_id = int(parts[0])
    days = parse_int(parts[1], minimum=1, maximum=3650, name="Количество дней")
    return user_id, days, parts[2] if len(parts) == 3 else "Выдано администратором"


def user_card(user: asyncpg.Record, timezone: str) -> str:
    name = " ".join(filter(None, [user["first_name"], user["last_name"]])) or "—"
    status = "активна ✅" if user["has_access"] else "неактивна ❌"
    return (
        "<b>Карточка пользователя</b>\n\n"
        f"ID: <code>{user['telegram_id']}</code>\n"
        f"Username: {('@' + safe(user['username'])) if user['username'] else '—'}\n"
        f"Имя: {safe(name)}\n"
        f"Язык: {safe(user['language_code'])}\n"
        f"Подписка: {status}\n"
        f"Доступ до: {format_datetime(user['access_until'], timezone)}\n"
        f"Платежей: {user['payment_count']} · Stars: {user['stars_paid']}\n"
        f"Покупок гайда: {user['guide_purchase_count']}\n"
        f"Покупки запрещены: {'да' if user['is_blocked'] else 'нет'}\n"
        f"Заблокировал бота: {'да' if user['bot_blocked'] else 'нет'}\n"
        f"Создан: {format_datetime(user['created_at'], timezone)}\n"
        f"Был в боте: {format_datetime(user['last_seen_at'], timezone)}"
    )


async def edit_admin(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise


def create_admin_router(settings: Settings) -> Router:
    router = Router(name="admin")
    router.message.filter(IsAdmin(settings.admin_id_set))
    router.callback_query.filter(IsAdmin(settings.admin_id_set))

    @router.message(Command("admin"))
    async def admin_home(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "<b>Панель администратора</b>\n\nВыберите раздел:",
            reply_markup=admin_keyboard(),
        )

    @router.callback_query(F.data == "adm:home")
    async def admin_home_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await edit_admin(
            callback,
            "<b>Панель администратора</b>\n\nВыберите раздел:",
            admin_keyboard(),
        )

    async def stats_text(database: Database) -> str:
        stats = await database.get_stats()
        return (
            "<b>Статистика</b>\n\n"
            f"Пользователей: <b>{stats.users}</b>\n"
            f"Новых за 24 ч.: <b>{stats.new_users_24h}</b>\n"
            f"Активных подписок: <b>{stats.active}</b>\n"
            f"Истёкших: <b>{stats.expired}</b>\n"
            f"Заблокировали бота: <b>{stats.blocked_bot}</b>\n\n"
            f"Успешных платежей: <b>{stats.payments}</b>\n"
            f"Получено Stars: <b>{stats.stars_total}</b>\n"
            f"Stars за 30 дней: <b>{stats.stars_30d}</b>\n\n"
            f"Продано гайдов: <b>{stats.guide_payments}</b>\n"
            f"Stars за гайды: <b>{stats.guide_stars_total}</b>"
        )

    @router.message(Command("stats"))
    async def stats_command(message: Message, database: Database) -> None:
        await message.answer(await stats_text(database), reply_markup=admin_back_keyboard())

    @router.callback_query(F.data == "adm:stats")
    async def stats_callback(callback: CallbackQuery, database: Database) -> None:
        await edit_admin(callback, await stats_text(database), admin_back_keyboard())

    async def users_text(database: Database, page: int, query: str | None = None):
        rows = await database.list_users(query, limit=10, offset=page * 10)
        full_count = rows[0]["full_count"] if rows else 0
        lines = [
            f"<b>Пользователи</b> · всего {full_count} · стр. {page + 1}\n"
        ]
        for user in rows:
            active = (
                "✅"
                if user["access_until"] and user["access_until"] > datetime.now(UTC)
                else "▫️"
            )
            username = (
                f"@{safe(user['username'])}"
                if user["username"]
                else safe(user["first_name"])
            )
            lines.append(f"{active} <code>{user['telegram_id']}</code> · {username}")
        if not rows:
            lines.append("Ничего не найдено.")
        lines.append("\nКарточка: <code>/user ID</code>")
        return "\n".join(lines), full_count

    @router.message(Command("users"))
    async def users_command(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        text, _ = await users_text(database, 0, command.args.strip() if command.args else None)
        await message.answer(text, reply_markup=admin_back_keyboard())

    @router.callback_query(F.data.startswith("adm:users:"))
    async def users_callback(callback: CallbackQuery, database: Database) -> None:
        try:
            page = max(0, int(callback.data.rsplit(":", 1)[1]))
        except ValueError:
            page = 0
        text, total = await users_text(database, page)
        await edit_admin(
            callback, text, users_keyboard(page, total > (page + 1) * 10)
        )

    @router.message(Command("user"))
    async def user_command(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        if not command.args or not command.args.strip().lstrip("-").isdigit():
            await message.answer("Использование: <code>/user USER_ID</code>")
            return
        user = await database.get_user(int(command.args.strip()))
        if user is None:
            await message.answer("Пользователь не найден.")
            return
        await message.answer(user_card(user, settings.timezone), reply_markup=admin_back_keyboard())

    @router.message(Command("grant", "extend"))
    async def grant_command(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        try:
            user_id, days, reason = parse_user_and_days(command.args)
            access_until = await database.grant_subscription(
                message.from_user.id, user_id, days, reason
            )
        except (ValueError, asyncpg.PostgresError) as exc:
            await message.answer(
                f"Ошибка: {safe(exc)}\nИспользование: <code>/grant USER_ID ДНИ [ПРИЧИНА]</code>"
            )
            return
        await message.answer(
            f"✅ Доступ <code>{user_id}</code> продлён до "
            f"{format_datetime(access_until, settings.timezone)}."
        )

    @router.message(Command("revoke", "remove"))
    async def revoke_command(
        message: Message,
        command: CommandObject,
        database: Database,
        access_service: AccessService,
    ) -> None:
        if not command.args:
            await message.answer("Использование: <code>/revoke USER_ID [ПРИЧИНА]</code>")
            return
        parts = command.args.split(maxsplit=1)
        try:
            user_id = int(parts[0])
        except ValueError:
            await message.answer("USER_ID должен быть числом.")
            return
        reason = parts[1] if len(parts) == 2 else "Отозвано администратором"
        changed = await database.revoke_subscription(message.from_user.id, user_id, reason)
        if not changed:
            await message.answer("Пользователь не найден.")
            return
        kicked = await access_service.remove_from_channel(user_id)
        if kicked:
            await database.mark_access_removed(user_id)
        await message.answer(
            f"✅ Доступ <code>{user_id}</code> отозван. "
            f"Удаление из канала: {'успешно' if kicked else 'не удалось'}."
        )

    @router.message(Command("kick"))
    async def kick_command(
        message: Message, command: CommandObject, access_service: AccessService, database: Database
    ) -> None:
        try:
            user_id = int(command.args or "")
        except ValueError:
            await message.answer("Использование: <code>/kick USER_ID</code>")
            return
        success = await access_service.remove_from_channel(user_id)
        if success:
            await database.mark_access_removed(user_id)
            await database.audit(message.from_user.id, "channel.kick", user_id)
        text = "✅ Пользователь удалён." if success else "Не удалось удалить пользователя."
        await message.answer(text)

    async def set_blocked(
        message: Message, command: CommandObject, database: Database, blocked: bool
    ) -> None:
        try:
            user_id = int(command.args or "")
        except ValueError:
            await message.answer(
                "Использование: "
                f"<code>/{'block' if blocked else 'unblock'} USER_ID</code>"
            )
            return
        changed = await database.set_user_blocked(
            message.from_user.id, user_id, blocked
        )
        text = "✅ Готово." if changed else "Пользователь не найден."
        await message.answer(text)

    @router.message(Command("block"))
    async def block_command(message: Message, command: CommandObject, database: Database) -> None:
        await set_blocked(message, command, database, True)

    @router.message(Command("unblock"))
    async def unblock_command(message: Message, command: CommandObject, database: Database) -> None:
        await set_blocked(message, command, database, False)

    async def payments_text(database: Database, limit: int = 10) -> str:
        rows = await database.list_payments(limit)
        lines = ["<b>Последние платежи</b>\n"]
        for payment in rows:
            icon = "✅" if payment["status"] == "paid" else "↩️"
            lines.append(
                f"{icon} {payment['amount_stars']} ⭐ · <code>{payment['user_id']}</code>\n"
                f"{safe(payment['product_title'])} · "
                f"{format_datetime(payment['created_at'], settings.timezone)}\n"
                f"<code>{safe(payment['telegram_charge_id'])}</code>"
            )
        return "\n\n".join(lines) if rows else "Платежей пока нет."

    @router.message(Command("payments"))
    async def payments_command(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        try:
            limit = parse_int(
                command.args or "10", minimum=1, maximum=20, name="Количество"
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            await payments_text(database, limit), reply_markup=admin_back_keyboard()
        )

    @router.callback_query(F.data == "adm:payments")
    async def payments_callback(callback: CallbackQuery, database: Database) -> None:
        await edit_admin(callback, await payments_text(database), admin_back_keyboard())

    @router.message(Command("refund"))
    async def refund_command(
        message: Message, command: CommandObject, bot: Bot, database: Database
    ) -> None:
        charge_id = command.args.strip() if command.args else ""
        if not charge_id:
            await message.answer("Использование: <code>/refund TELEGRAM_CHARGE_ID</code>")
            return
        payment = await database.get_any_payment(charge_id)
        if payment is None:
            await message.answer("Платёж не найден.")
            return
        if payment["status"] != "paid":
            await message.answer(f"Возврат невозможен: статус {safe(payment['status'])}.")
            return
        try:
            await bot.refund_star_payment(
                user_id=payment["user_id"], telegram_payment_charge_id=charge_id
            )
        except TelegramBadRequest as exc:
            await message.answer(f"Telegram отклонил возврат: {safe(exc)}")
            return
        if await database.get_guide_payment(charge_id) is not None:
            await database.mark_guide_payment_refunded(message.from_user.id, charge_id)
            await message.answer("✅ Stars за гайд возвращены; ссылка больше не выдаётся.")
        else:
            new_until = await database.mark_payment_refunded(
                message.from_user.id, charge_id
            )
            await message.answer(
                "✅ Stars возвращены. Новый срок доступа: "
                f"{format_datetime(new_until, settings.timezone)}."
            )

    async def plans_text(database: Database) -> str:
        rows = await database.get_plans()
        lines = ["<b>Тарифы</b>\n"]
        for plan in rows:
            icon = "✅" if plan["is_active"] else "⛔"
            lines.append(
                f"{icon} <code>{safe(plan['code'])}</code> · {safe(plan['title'])}\n"
                f"{plan['price_stars']} ⭐ · {plan['duration_days']} дн."
            )
        lines.append(
            "\nДобавить: <code>/plan_add CODE STARS ДНИ НАЗВАНИЕ</code>\n"
            "Вкл./выкл.: <code>/plan_toggle CODE</code>"
        )
        return "\n\n".join(lines)

    @router.message(Command("plans_admin"))
    async def plans_admin(message: Message, database: Database) -> None:
        await message.answer(await plans_text(database), reply_markup=admin_back_keyboard())

    @router.callback_query(F.data == "adm:plans")
    async def plans_callback(callback: CallbackQuery, database: Database) -> None:
        await edit_admin(callback, await plans_text(database), admin_back_keyboard())

    @router.message(Command("plan_add"))
    async def plan_add(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        parts = command.args.split(maxsplit=3) if command.args else []
        if len(parts) != 4:
            await message.answer("Использование: <code>/plan_add CODE STARS ДНИ НАЗВАНИЕ</code>")
            return
        try:
            price = parse_int(parts[1], minimum=1, maximum=10000, name="Цена")
            days = parse_int(parts[2], minimum=1, maximum=3650, name="Дни")
            plan = await database.create_plan(
                message.from_user.id, parts[0], parts[3], price, days
            )
        except (ValueError, asyncpg.UniqueViolationError, asyncpg.CheckViolationError) as exc:
            await message.answer(f"Не удалось создать тариф: {safe(exc)}")
            return
        await message.answer(f"✅ Тариф <code>{safe(plan['code'])}</code> создан.")

    @router.message(Command("plan_toggle"))
    async def plan_toggle(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        if not command.args:
            await message.answer("Использование: <code>/plan_toggle CODE</code>")
            return
        plan = await database.toggle_plan(message.from_user.id, command.args.strip())
        if plan is None:
            await message.answer("Тариф не найден.")
            return
        await message.answer(
            f"✅ <code>{safe(plan['code'])}</code>: "
            f"{'включён' if plan['is_active'] else 'выключен'}."
        )

    async def promos_text(database: Database) -> str:
        rows = await database.list_promos()
        lines = ["<b>Промокоды</b>\n"]
        for promo in rows:
            icon = "✅" if promo["is_active"] else "⛔"
            limit = promo["max_uses"] if promo["max_uses"] is not None else "∞"
            lines.append(
                f"{icon} <code>{safe(promo['code'])}</code> · {promo['duration_days']} дн. · "
                f"{promo['used_count']}/{limit}"
            )
        lines.append(
            "\nСоздать: <code>/promo_add CODE ДНИ ЛИМИТ</code> (0 = без лимита)\n"
            "Отключить: <code>/promo_disable CODE</code>"
        )
        return "\n".join(lines)

    @router.message(Command("promos"))
    async def promos_command(message: Message, database: Database) -> None:
        await message.answer(await promos_text(database), reply_markup=admin_back_keyboard())

    @router.callback_query(F.data == "adm:promos")
    async def promos_callback(callback: CallbackQuery, database: Database) -> None:
        await edit_admin(callback, await promos_text(database), admin_back_keyboard())

    @router.message(Command("promo_add"))
    async def promo_add(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        parts = command.args.split() if command.args else []
        if len(parts) != 3:
            await message.answer("Использование: <code>/promo_add CODE ДНИ ЛИМИТ</code>")
            return
        try:
            days = parse_int(parts[1], minimum=1, maximum=3650, name="Дни")
            limit_value = parse_int(parts[2], minimum=0, maximum=1_000_000, name="Лимит")
            await database.create_promo(
                message.from_user.id, parts[0], days, limit_value or None
            )
        except (ValueError, asyncpg.UniqueViolationError, asyncpg.CheckViolationError) as exc:
            await message.answer(f"Не удалось создать промокод: {safe(exc)}")
            return
        await message.answer(f"✅ Промокод <code>{safe(parts[0].upper())}</code> создан.")

    @router.message(Command("promo_disable"))
    async def promo_disable(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        if not command.args:
            await message.answer("Использование: <code>/promo_disable CODE</code>")
            return
        changed = await database.disable_promo(message.from_user.id, command.args.strip())
        await message.answer("✅ Промокод отключён." if changed else "Промокод не найден.")

    async def begin_broadcast(message: Message, state: FSMContext, segment: str) -> None:
        if segment not in {"all", "active", "expired"}:
            await message.answer("Сегмент: all, active или expired.")
            return
        await state.set_state(AdminBroadcast.waiting_content)
        await state.update_data(segment=segment)
        await message.answer(
            f"Сегмент: <b>{segment}</b>. Отправьте следующим сообщением текст, фото, "
            "видео или другой материал для рассылки. /cancel — отмена."
        )

    @router.message(Command("broadcast"))
    async def broadcast_command(
        message: Message, command: CommandObject, state: FSMContext
    ) -> None:
        await begin_broadcast(message, state, (command.args or "all").strip().lower())

    @router.callback_query(F.data == "adm:broadcast")
    async def broadcast_callback(callback: CallbackQuery) -> None:
        await edit_admin(
            callback,
            "<b>Новая рассылка</b>\n\nВыберите получателей:",
            broadcast_segments_keyboard(),
        )

    @router.callback_query(
        F.data.in_(
            {"adm:broadcast:all", "adm:broadcast:active", "adm:broadcast:expired"}
        )
    )
    async def broadcast_segment(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        segment = callback.data.rsplit(":", 1)[1]
        if callback.message:
            await begin_broadcast(callback.message, state, segment)

    @router.message(AdminBroadcast.waiting_content)
    async def broadcast_content(message: Message, state: FSMContext, database: Database) -> None:
        if message.text and message.text.startswith("/cancel"):
            await state.clear()
            await message.answer("Отменено.")
            return
        data = await state.get_data()
        broadcast_id = await database.create_broadcast(
            message.from_user.id, message.chat.id, message.message_id, data["segment"]
        )
        await state.clear()
        await message.answer(
            f"Предпросмотр рассылки #{broadcast_id} для сегмента <b>{data['segment']}</b>:"
        )
        await message.bot.copy_message(
            chat_id=message.chat.id, from_chat_id=message.chat.id, message_id=message.message_id
        )
        await message.answer(
            "Запустить рассылку?", reply_markup=broadcast_confirm_keyboard(broadcast_id)
        )

    @router.callback_query(F.data.startswith("adm:broadcast:confirm:"))
    async def broadcast_confirm(
        callback: CallbackQuery, database: Database, broadcast_service: BroadcastService
    ) -> None:
        broadcast_id = int(callback.data.rsplit(":", 1)[1])
        queued = await database.queue_broadcast(callback.from_user.id, broadcast_id)
        if queued:
            broadcast_service.start(broadcast_id)
            await edit_admin(
                callback,
                f"✅ Рассылка #{broadcast_id} запущена в фоне. Итог придёт отдельным сообщением.",
                admin_back_keyboard(),
            )
        else:
            await callback.answer("Рассылка уже запущена или отменена.", show_alert=True)

    @router.callback_query(F.data.startswith("adm:broadcast:cancel:"))
    async def broadcast_cancel(callback: CallbackQuery, database: Database) -> None:
        broadcast_id = int(callback.data.rsplit(":", 1)[1])
        await database.cancel_broadcast(callback.from_user.id, broadcast_id)
        await edit_admin(callback, "Рассылка отменена.", admin_back_keyboard())

    @router.message(Command("notify"))
    async def notify_command(
        message: Message, command: CommandObject, bot: Bot, database: Database
    ) -> None:
        parts = command.args.split(maxsplit=1) if command.args else []
        if len(parts) != 2:
            await message.answer("Использование: <code>/notify USER_ID ТЕКСТ</code>")
            return
        try:
            user_id = int(parts[0])
            await bot.send_message(user_id, parts[1])
        except (ValueError, TelegramBadRequest, TelegramForbiddenError) as exc:
            await message.answer(f"Не отправлено: {safe(exc)}")
            return
        await database.audit(message.from_user.id, "user.notify", user_id)
        await message.answer("✅ Сообщение отправлено.")

    @router.message(Command("export"))
    async def export_command(message: Message, database: Database) -> None:
        rows = await database.export_users()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "telegram_id",
                "username",
                "first_name",
                "last_name",
                "language",
                "access_until",
                "is_blocked",
                "bot_blocked",
                "created_at",
                "last_seen_at",
            ]
        )
        for row in rows:
            writer.writerow(list(row))
        data = output.getvalue().encode("utf-8-sig")
        filename = f"users-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.csv"
        await message.answer_document(
            BufferedInputFile(data, filename=filename), caption=f"Пользователей: {len(rows)}"
        )
        await database.audit(message.from_user.id, "users.export", details={"count": len(rows)})

    @router.message(Command("audit"))
    async def audit_command(
        message: Message, command: CommandObject, database: Database
    ) -> None:
        try:
            limit = parse_int(command.args or "20", minimum=1, maximum=50, name="Количество")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        rows = await database.audit_log(limit)
        lines = ["<b>Журнал действий</b>\n"]
        for row in rows:
            details = json.dumps(row["details"], ensure_ascii=False)
            lines.append(
                f"{format_datetime(row['created_at'], settings.timezone)} · "
                f"<code>{safe(row['action'])}</code>\n"
                f"admin {row['admin_id']} · user {row['target_user_id'] or '—'} · "
                f"{safe(details[:200])}"
            )
        await message.answer("\n\n".join(lines))

    async def health_text(bot: Bot, database: Database) -> str:
        db_ok = await database.health()
        try:
            me = await bot.get_me()
            bot_status = f"✅ @{safe(me.username)}"
        except Exception as exc:
            bot_status = f"❌ {safe(exc)}"
        return (
            "<b>Состояние системы</b>\n\n"
            f"Telegram API: {bot_status}\n"
            f"PostgreSQL: {'✅ доступен' if db_ok else '❌ недоступен'}\n"
            f"Версия: 4.0.0\n"
            f"Время: {format_datetime(datetime.now(UTC), settings.timezone)}"
        )

    @router.message(Command("health"))
    async def health_command(message: Message, bot: Bot, database: Database) -> None:
        await message.answer(await health_text(bot, database), reply_markup=admin_back_keyboard())

    @router.callback_query(F.data == "adm:health")
    async def health_callback(callback: CallbackQuery, bot: Bot, database: Database) -> None:
        await edit_admin(callback, await health_text(bot, database), admin_back_keyboard())

    @router.message(Command("settings"))
    async def settings_command(message: Message) -> None:
        await message.answer(
            "<b>Настройки</b>\n\n"
            f"Канал: <code>{safe(settings.channel_id)}</code>\n"
            f"Администраторы: {', '.join(map(str, settings.admin_ids))}\n"
            f"Часовой пояс: {safe(settings.timezone)}\n"
            f"Язык по умолчанию: {settings.default_language}\n"
            f"Скорость рассылки: {settings.broadcast_rate_per_second}/с\n"
            f"Поддержка: {safe(settings.support_username)}\n"
            f"Гайд: {'✅ включён' if settings.guide_enabled else '❌ выключен'}\n"
            f"Цена гайда: {settings.guide_price_stars} ⭐\n\n"
            "Токен и строка подключения намеренно не показываются."
        )

    @router.message(Command("admin_help"))
    async def admin_help(message: Message) -> None:
        await message.answer(ADMIN_HELP, reply_markup=admin_back_keyboard())

    @router.callback_query(F.data == "adm:help")
    async def admin_help_callback(callback: CallbackQuery) -> None:
        await edit_admin(callback, ADMIN_HELP, admin_back_keyboard())

    @router.message(Command("cancel"))
    async def cancel_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Текущий диалог отменён.", reply_markup=admin_keyboard())

    return router
