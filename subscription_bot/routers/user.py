from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from subscription_bot.config import Settings
from subscription_bot.database import Database, utcnow
from subscription_bot.keyboards import invite_keyboard, language_keyboard, plans_keyboard
from subscription_bot.locales import normalize_language, tr
from subscription_bot.services import AccessService
from subscription_bot.utils import days_remaining, format_datetime

router = Router(name="user")


async def send_plans(message: Message, database: Database) -> None:
    language = await database.get_language(message.from_user.id)
    plans = await database.get_active_plans()
    if not plans:
        await message.answer(tr(language, "no_plans"))
        return
    await message.answer(
        tr(language, "plans_title"), reply_markup=plans_keyboard(plans, language)
    )


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, database: Database) -> None:
    language = await database.get_language(message.from_user.id)
    if command.args and command.args.startswith("promo_"):
        code = command.args.removeprefix("promo_")
        result = await database.redeem_promo(message.from_user.id, code)
        key = f"promo_{result.message}"
        if result.ok and result.access_until:
            await message.answer(
                tr(
                    language,
                    "promo_ok",
                    date=format_datetime(result.access_until, database.settings.timezone),
                )
            )
        elif key in {
            f"promo_{name}"
            for name in ("not_found", "expired", "exhausted", "already_used", "blocked")
        }:
            await message.answer(tr(language, key))
    await message.answer(tr(language, "welcome"))
    await send_plans(message, database)


@router.message(Command("plans"))
async def plans(message: Message, database: Database) -> None:
    await send_plans(message, database)


@router.message(Command("my"))
async def subscription_status(message: Message, database: Database) -> None:
    language = await database.get_language(message.from_user.id)
    user = await database.get_user(message.from_user.id)
    if not user or not user["has_access"]:
        await message.answer(tr(language, "my_inactive"))
        return
    await message.answer(
        tr(
            language,
            "my_active",
            date=format_datetime(user["access_until"], database.settings.timezone),
            days=days_remaining(user["access_until"]),
        )
    )


@router.message(Command("invite"))
async def invite(
    message: Message, database: Database, access_service: AccessService
) -> None:
    language = await database.get_language(message.from_user.id)
    user = await database.get_user(message.from_user.id)
    if not user or not user["has_access"]:
        await message.answer(tr(language, "my_inactive"))
        return
    link = await access_service.create_invite(message.from_user.id, user["access_until"])
    await message.answer(
        tr(language, "invite_ready"), reply_markup=invite_keyboard(link, language)
    )


@router.message(Command("promo"))
async def promo(message: Message, command: CommandObject, database: Database) -> None:
    language = await database.get_language(message.from_user.id)
    if not command.args:
        await message.answer(tr(language, "promo_usage"))
        return
    result = await database.redeem_promo(message.from_user.id, command.args.strip())
    key = f"promo_{result.message}"
    if result.ok and result.access_until:
        await message.answer(
            tr(
                language,
                "promo_ok",
                date=format_datetime(result.access_until, database.settings.timezone),
            )
        )
    else:
        await message.answer(tr(language, key))


@router.message(Command("language"))
async def language(message: Message, database: Database) -> None:
    current = await database.get_language(message.from_user.id)
    await message.answer(tr(current, "language_title"), reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def set_language(callback: CallbackQuery, database: Database) -> None:
    selected = normalize_language(
        callback.data.split(":", 1)[1], database.settings.default_language
    )
    await database.set_language(callback.from_user.id, selected)
    await callback.answer(tr(selected, "language_saved"), show_alert=True)
    if callback.message:
        await callback.message.edit_text(
            tr(selected, "language_saved"), reply_markup=language_keyboard()
        )


@router.message(Command("help"))
async def help_command(message: Message, database: Database) -> None:
    language = await database.get_language(message.from_user.id)
    await message.answer(tr(language, "help"))


@router.message(Command("terms"))
async def terms(message: Message, database: Database, settings: Settings) -> None:
    language = await database.get_language(message.from_user.id)
    await message.answer(tr(language, "terms", url=settings.terms_url))


@router.message(Command("support", "paysupport"))
async def payment_support(message: Message, database: Database, settings: Settings) -> None:
    language = await database.get_language(message.from_user.id)
    await message.answer(tr(language, "support", support=settings.support_username))


@router.message(Command("ping"))
async def ping(message: Message) -> None:
    await message.answer(f"pong · {utcnow().strftime('%H:%M:%S UTC')}")
