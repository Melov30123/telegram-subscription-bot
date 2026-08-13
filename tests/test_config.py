import pytest
from pydantic import ValidationError

from subscription_bot.config import Settings


def make_settings(**overrides) -> Settings:
    values = {
        "bot_token": "123456:token",
        "database_url": "postgres://user:pass@localhost/db",
        "channel_id": -100123,
        "admin_ids": "10,20,10",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_settings_normalize_database_and_admins() -> None:
    settings = make_settings(timezone="Moscow")
    assert settings.database_url.startswith("postgresql://")
    assert settings.admin_ids == [10, 20]
    assert settings.admin_id_set == {10, 20}
    assert settings.timezone == "Europe/Moscow"


def test_settings_accept_single_numeric_admin_id() -> None:
    settings = make_settings(admin_ids=1477505537)
    assert settings.admin_ids == [1477505537]


def test_guide_settings_and_trailing_comma() -> None:
    settings = make_settings(
        guide_download_url="https://drive.google.com/file/d/example/view,",
        guide_price_stars=250,
        guide_title="My guide",
    )
    assert settings.guide_enabled is True
    assert settings.guide_download_url == "https://drive.google.com/file/d/example/view"
    assert settings.guide_price_stars == 250


def test_guide_is_disabled_without_url() -> None:
    assert make_settings(guide_download_url="").guide_enabled is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("//drive.google.com/file/d/example/view", "https://drive.google.com/file/d/example/view"),
        ("drive.google.com/file/d/example/view", "https://drive.google.com/file/d/example/view"),
    ],
)
def test_guide_url_adds_missing_https(value: str, expected: str) -> None:
    assert make_settings(guide_download_url=value).guide_download_url == expected


def test_settings_reject_invalid_pool() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_pool_min_size=10, database_pool_max_size=2)
