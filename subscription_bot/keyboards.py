from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from subscription_bot.locales import tr


def plans_keyboard(plans: Sequence[object], language: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=tr(
                language,
                "buy_button",
                title=plan["title"],
                price=plan["price_stars"],
                days=plan["duration_days"],
            ),
            callback_data=f"buy:{plan['id']}",
        )
    builder.adjust(1)
    return builder.as_markup()


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en"),
                InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang:es"),
            ]
        ]
    )


def invite_keyboard(link: str, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr(language, "invite_button"), url=link)]
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users:0"),
            ],
            [
                InlineKeyboardButton(text="💳 Платежи", callback_data="adm:payments"),
                InlineKeyboardButton(text="🧾 Тарифы", callback_data="adm:plans"),
            ],
            [
                InlineKeyboardButton(text="🎁 Промокоды", callback_data="adm:promos"),
                InlineKeyboardButton(text="📨 Рассылка", callback_data="adm:broadcast"),
            ],
            [
                InlineKeyboardButton(text="🩺 Система", callback_data="adm:health"),
                InlineKeyboardButton(text="📖 Все команды", callback_data="adm:help"),
            ],
        ]
    )


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="← Админка", callback_data="adm:home")]]
    )


def users_keyboard(page: int, has_next: bool) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton(text="←", callback_data=f"adm:users:{page - 1}"))
    if has_next:
        row.append(InlineKeyboardButton(text="→", callback_data=f"adm:users:{page + 1}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton(text="← Админка", callback_data="adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_segments_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все", callback_data="adm:broadcast:all")],
            [
                InlineKeyboardButton(
                    text="С активной подпиской", callback_data="adm:broadcast:active"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Без подписки / истёкшие", callback_data="adm:broadcast:expired"
                )
            ],
            [InlineKeyboardButton(text="← Админка", callback_data="adm:home")],
        ]
    )


def broadcast_confirm_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Запустить", callback_data=f"adm:broadcast:confirm:{broadcast_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"adm:broadcast:cancel:{broadcast_id}"
                ),
            ]
        ]
    )
