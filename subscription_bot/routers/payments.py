from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from subscription_bot.config import Settings
from subscription_bot.database import Database
from subscription_bot.keyboards import invite_keyboard
from subscription_bot.locales import tr
from subscription_bot.services import AccessService
from subscription_bot.utils import format_datetime, parse_payment_payload

router = Router(name="payments")
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery, bot: Bot, database: Database) -> None:
    language = await database.get_language(callback.from_user.id)
    user = await database.get_user(callback.from_user.id)
    if user and user["is_blocked"]:
        await callback.answer(tr(language, "blocked"), show_alert=True)
        return
    try:
        plan_id = int(callback.data.split(":", 1)[1])
        plan = await database.get_plan(plan_id)
        if plan is None or not plan["is_active"]:
            raise ValueError("Plan unavailable")
        intent = await database.create_payment_intent(callback.from_user.id, plan_id)
    except (ValueError, IndexError):
        await callback.answer(tr(language, "no_plans"), show_alert=True)
        return

    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=tr(language, "invoice_title", title=plan["title"]),
        description=tr(language, "invoice_description", days=plan["duration_days"]),
        payload=f"sub:{intent['id']}",
        currency="XTR",
        prices=[LabeledPrice(label=plan["title"], amount=plan["price_stars"])],
        start_parameter=f"plan-{plan['code']}",
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, database: Database) -> None:
    try:
        intent_id = parse_payment_payload(query.invoice_payload)
        async with asyncio.timeout(7):
            valid = await database.validate_payment_intent(
                intent_id, query.from_user.id, query.total_amount, query.currency
            )
    except Exception:
        logger.exception("Pre-checkout validation failed for user %s", query.from_user.id)
        valid = False
    language = await database.get_language(query.from_user.id)
    await query.answer(
        ok=valid,
        error_message=None if valid else tr(language, "payment_invalid"),
    )


@router.message(F.successful_payment)
async def successful_payment(
    message: Message,
    database: Database,
    access_service: AccessService,
    settings: Settings,
) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    language = await database.get_language(message.from_user.id)
    try:
        intent_id = parse_payment_payload(payment.invoice_payload)
        result = await database.complete_payment(
            intent_id=intent_id,
            user_id=message.from_user.id,
            telegram_charge_id=payment.telegram_payment_charge_id,
            provider_charge_id=payment.provider_payment_charge_id,
            amount=payment.total_amount,
            currency=payment.currency,
            raw_data=payment.model_dump(mode="json"),
        )
    except Exception:
        logger.exception(
            "Critical payment processing error: user=%s charge=%s",
            message.from_user.id,
            payment.telegram_payment_charge_id,
        )
        await message.answer(tr(language, "payment_manual_review"))
        for admin_id in settings.admin_ids:
            try:
                await message.bot.send_message(
                    admin_id,
                    "⚠️ <b>Платёж требует ручной проверки</b>\n\n"
                    f"User: <code>{message.from_user.id}</code>\n"
                    f"Stars: {payment.total_amount}\n"
                    f"Charge: <code>{payment.telegram_payment_charge_id}</code>",
                )
            except Exception:
                logger.exception("Could not alert administrator %s", admin_id)
        return

    key = "payment_duplicate" if result.already_processed else "payment_ok"
    await message.answer(
        tr(
            language,
            key,
            date=format_datetime(result.access_until, database.settings.timezone),
        )
    )
    try:
        link = await access_service.create_invite(message.from_user.id, result.access_until)
        await message.answer(
            tr(language, "invite_ready"), reply_markup=invite_keyboard(link, language)
        )
    except TelegramBadRequest:
        logger.exception("Could not create channel invite for %s", message.from_user.id)
        await message.answer(tr(language, "invite_error"))


@router.message(F.refunded_payment)
async def refunded_payment(message: Message, database: Database) -> None:
    refunded = message.refunded_payment
    if refunded is None:
        return
    payment = await database.get_payment(refunded.telegram_payment_charge_id)
    if payment is None or payment["status"] != "paid":
        return
    new_until = await database.mark_payment_refunded(
        0, refunded.telegram_payment_charge_id
    )
    logger.warning(
        "Telegram reported a refund: user=%s charge=%s access_until=%s",
        payment["user_id"],
        refunded.telegram_payment_charge_id,
        new_until,
    )
