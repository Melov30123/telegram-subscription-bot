from subscription_bot.locales import normalize_language, tr


def test_normalize_language() -> None:
    assert normalize_language("es-MX") == "es"
    assert normalize_language("de", "en") == "en"
    assert normalize_language(None, "ru") == "ru"


def test_every_supported_language_has_core_messages() -> None:
    for language in ("ru", "en", "es"):
        assert tr(language, "plans_title")
        assert "100" in tr(language, "buy_button", title="30", price=100, days=30)
