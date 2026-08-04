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


def test_settings_reject_invalid_pool() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_pool_min_size=10, database_pool_max_size=2)
