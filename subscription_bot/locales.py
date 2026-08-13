from __future__ import annotations

from typing import Any

SUPPORTED_LANGUAGES = {"ru", "en", "es"}

MESSAGES: dict[str, dict[str, str]] = {
    "ru": {
        "welcome": (
            "<b>Добро пожаловать!</b>\n\n"
            "Здесь можно оформить доступ к закрытому каналу через Telegram Stars. "
            "Выберите тариф — после оплаты бот сразу выдаст персональную ссылку."
        ),
        "plans_title": "<b>Выберите тариф</b>\n\nОплата проходит безопасно внутри Telegram:",
        "no_plans": "Сейчас нет доступных тарифов. Попробуйте немного позже.",
        "buy_button": "⭐ {title} · {price} Stars · {days} дн.",
        "guide_offer": (
            "<b>📘 Купить гайд</b>\n\n"
            "После оплаты бот сразу выдаст ссылку на скачивание с Google Drive.\n"
            "Цена: <b>{price} ⭐</b>"
        ),
        "guide_buy_button": "📘 Купить гайд · {price} ⭐",
        "guide_invoice_title": "{title}",
        "guide_invoice_description": "Цифровой гайд со ссылкой на скачивание",
        "guide_payment_ok": "✅ <b>Гайд оплачен</b>\n\nСсылка на скачивание доступна ниже.",
        "guide_already_bought": "✅ Вы уже покупали гайд. Ссылка доступна ниже.",
        "guide_download_button": "📥 Скачать гайд",
        "guide_unavailable": "Сейчас покупка гайда временно недоступна.",
        "guide_not_bought": "Вы ещё не покупали гайд. Оформить покупку можно ниже.",
        "invoice_title": "Подписка: {title}",
        "invoice_description": "Доступ к закрытому каналу на {days} дней",
        "payment_invalid": "Платёж устарел или его параметры изменились. Создайте новый счёт.",
        "payment_ok": "✅ <b>Оплата получена</b>\n\nДоступ активен до {date}.",
        "payment_duplicate": "Этот платёж уже был обработан. Доступ активен до {date}.",
        "invite_error": (
            "Оплата сохранена, но Telegram не позволил создать ссылку. "
            "Используйте /invite или обратитесь в поддержку."
        ),
        "payment_manual_review": (
            "⚠️ Telegram подтвердил оплату, но автоматическая выдача не завершилась. "
            "Администратор уже уведомлён. Сохраните это сообщение и обратитесь в поддержку."
        ),
        "my_active": "✅ <b>Подписка активна</b>\n\nДоступ до: {date}\nОсталось: {days}",
        "my_inactive": "У вас нет активной подписки. Выберите тариф через /plans.",
        "blocked": "Доступ к покупкам ограничен администратором. Обратитесь в поддержку.",
        "invite_button": "📢 Войти в канал",
        "invite_ready": "Персональная ссылка действует 24 часа и рассчитана на одного участника.",
        "language_title": "Выберите язык интерфейса:",
        "language_saved": "Язык сохранён.",
        "help": (
            "<b>Команды</b>\n"
            "/plans — тарифы и покупка\n"
            "/guide — купить или скачать гайд\n"
            "/my — состояние подписки\n"
            "/invite — новая ссылка в канал\n"
            "/promo CODE — применить промокод\n"
            "/language — сменить язык\n"
            "/terms — условия\n"
            "/paysupport — помощь с оплатой"
        ),
        "support": "По вопросам оплаты и доступа напишите: {support}",
        "terms": "Условия использования и возврата: {url}",
        "promo_usage": "Использование: <code>/promo CODE</code>",
        "promo_ok": "🎁 Промокод применён. Доступ активен до {date}.",
        "promo_not_found": "Промокод не найден или отключён.",
        "promo_expired": "Срок действия промокода закончился.",
        "promo_exhausted": "Лимит активаций промокода исчерпан.",
        "promo_already_used": "Вы уже использовали этот промокод.",
        "promo_blocked": "Активация промокодов ограничена. Обратитесь в поддержку.",
        "reminder": "⏳ Подписка закончится через {days}. Продлить: /plans",
    },
    "en": {
        "welcome": (
            "<b>Welcome!</b>\n\n"
            "Get access to the private channel with Telegram Stars. Choose a plan and "
            "the bot will send your personal invite immediately after payment."
        ),
        "plans_title": "<b>Choose a plan</b>\n\nPayment is processed securely by Telegram:",
        "no_plans": "No plans are available right now. Please try again later.",
        "buy_button": "⭐ {title} · {price} Stars · {days} days",
        "guide_offer": (
            "<b>📘 Buy the guide</b>\n\n"
            "After payment, the bot will immediately provide the Google Drive download link.\n"
            "Price: <b>{price} ⭐</b>"
        ),
        "guide_buy_button": "📘 Buy guide · {price} ⭐",
        "guide_invoice_title": "{title}",
        "guide_invoice_description": "Digital guide with a download link",
        "guide_payment_ok": "✅ <b>Guide purchased</b>\n\nYour download link is below.",
        "guide_already_bought": "✅ You already purchased the guide. Your link is below.",
        "guide_download_button": "📥 Download guide",
        "guide_unavailable": "Guide purchases are temporarily unavailable.",
        "guide_not_bought": "You have not purchased the guide yet. Buy it below.",
        "invoice_title": "Subscription: {title}",
        "invoice_description": "Private channel access for {days} days",
        "payment_invalid": "This invoice is invalid or expired. Please create a new one.",
        "payment_ok": "✅ <b>Payment received</b>\n\nAccess is active until {date}.",
        "payment_duplicate": "This payment was already processed. Access is active until {date}.",
        "invite_error": (
            "Payment is saved, but Telegram could not create an invite. "
            "Use /invite or contact support."
        ),
        "payment_manual_review": (
            "⚠️ Telegram confirmed the payment, but access was not issued automatically. "
            "An administrator has been notified. Please contact support."
        ),
        "my_active": "✅ <b>Subscription active</b>\n\nAccess until: {date}\nRemaining: {days}",
        "my_inactive": "You do not have an active subscription. Choose one with /plans.",
        "blocked": "Purchases are restricted by an administrator. Please contact support.",
        "invite_button": "📢 Join the channel",
        "invite_ready": "Your one-person invite is valid for 24 hours.",
        "language_title": "Choose your interface language:",
        "language_saved": "Language saved.",
        "help": (
            "<b>Commands</b>\n"
            "/plans — plans and purchase\n"
            "/guide — buy or download the guide\n"
            "/my — subscription status\n"
            "/invite — new channel invite\n"
            "/promo CODE — redeem a promo code\n"
            "/language — change language\n"
            "/terms — terms\n"
            "/paysupport — payment support"
        ),
        "support": "For payment or access help, contact: {support}",
        "terms": "Terms and refund policy: {url}",
        "promo_usage": "Usage: <code>/promo CODE</code>",
        "promo_ok": "🎁 Promo applied. Access is active until {date}.",
        "promo_not_found": "Promo code not found or disabled.",
        "promo_expired": "This promo code has expired.",
        "promo_exhausted": "This promo code has reached its usage limit.",
        "promo_already_used": "You have already used this promo code.",
        "promo_blocked": "Promo activation is restricted. Please contact support.",
        "reminder": "⏳ Your subscription ends in {days} days. Renew: /plans",
    },
    "es": {
        "welcome": (
            "<b>¡Bienvenido!</b>\n\n"
            "Obtén acceso al canal privado con Telegram Stars. Elige un plan y el bot "
            "enviará tu enlace personal inmediatamente después del pago."
        ),
        "plans_title": "<b>Elige un plan</b>\n\nEl pago se procesa de forma segura en Telegram:",
        "no_plans": "No hay planes disponibles ahora. Inténtalo más tarde.",
        "buy_button": "⭐ {title} · {price} Stars · {days} días",
        "guide_offer": (
            "<b>📘 Comprar la guía</b>\n\n"
            "Después del pago recibirás inmediatamente el enlace de Google Drive.\n"
            "Precio: <b>{price} ⭐</b>"
        ),
        "guide_buy_button": "📘 Comprar guía · {price} ⭐",
        "guide_invoice_title": "{title}",
        "guide_invoice_description": "Guía digital con enlace de descarga",
        "guide_payment_ok": "✅ <b>Guía comprada</b>\n\nTu enlace de descarga está abajo.",
        "guide_already_bought": "✅ Ya compraste la guía. Tu enlace está abajo.",
        "guide_download_button": "📥 Descargar guía",
        "guide_unavailable": "La compra de la guía no está disponible temporalmente.",
        "guide_not_bought": "Todavía no has comprado la guía. Puedes comprarla abajo.",
        "invoice_title": "Suscripción: {title}",
        "invoice_description": "Acceso al canal privado durante {days} días",
        "payment_invalid": "Esta factura no es válida o ha caducado. Crea una nueva.",
        "payment_ok": "✅ <b>Pago recibido</b>\n\nAcceso activo hasta {date}.",
        "payment_duplicate": "Este pago ya fue procesado. Acceso activo hasta {date}.",
        "invite_error": (
            "El pago está guardado, pero Telegram no pudo crear el enlace. "
            "Usa /invite o contacta con soporte."
        ),
        "payment_manual_review": (
            "⚠️ Telegram confirmó el pago, pero el acceso no se emitió automáticamente. "
            "Se ha avisado al administrador. Contacta con soporte."
        ),
        "my_active": "✅ <b>Suscripción activa</b>\n\nAcceso hasta: {date}\nRestante: {days}",
        "my_inactive": "No tienes una suscripción activa. Elige una con /plans.",
        "blocked": "Las compras están restringidas. Contacta con soporte.",
        "invite_button": "📢 Entrar al canal",
        "invite_ready": "Tu enlace para una persona es válido durante 24 horas.",
        "language_title": "Elige el idioma de la interfaz:",
        "language_saved": "Idioma guardado.",
        "help": (
            "<b>Comandos</b>\n"
            "/plans — planes y compra\n"
            "/guide — comprar o descargar la guía\n"
            "/my — estado de suscripción\n"
            "/invite — nuevo enlace al canal\n"
            "/promo CODE — activar un código\n"
            "/language — cambiar idioma\n"
            "/terms — condiciones\n"
            "/paysupport — ayuda con pagos"
        ),
        "support": "Para ayuda con pagos o acceso: {support}",
        "terms": "Condiciones y política de reembolso: {url}",
        "promo_usage": "Uso: <code>/promo CODE</code>",
        "promo_ok": "🎁 Código aplicado. Acceso activo hasta {date}.",
        "promo_not_found": "Código no encontrado o desactivado.",
        "promo_expired": "Este código ha caducado.",
        "promo_exhausted": "Se alcanzó el límite de usos.",
        "promo_already_used": "Ya has usado este código.",
        "promo_blocked": "La activación está restringida. Contacta con soporte.",
        "reminder": "⏳ Tu suscripción termina en {days} días. Renovar: /plans",
    },
}


def normalize_language(language: str | None, default: str = "ru") -> str:
    candidate = (language or default).split("-")[0].lower()
    return candidate if candidate in SUPPORTED_LANGUAGES else default


def tr(language: str, key: str, **kwargs: Any) -> str:
    normalized = normalize_language(language)
    template = MESSAGES.get(normalized, MESSAGES["ru"]).get(key, MESSAGES["ru"][key])
    return template.format(**kwargs)
