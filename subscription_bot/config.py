from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BeforeValidator, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_admin_ids(value: object) -> list[int] | object:
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    return value


AdminIds = Annotated[list[int], BeforeValidator(_parse_admin_ids)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str
    database_url: str
    channel_id: int | str
    admin_ids: AdminIds

    default_plan_code: str = "monthly"
    default_plan_title: str = "30 days"
    default_plan_price_stars: int = Field(default=100, ge=1, le=10000)
    default_plan_duration_days: int = Field(default=30, ge=1, le=3650)

    guide_download_url: str | None = None
    guide_price_stars: int = Field(default=500, ge=1, le=10000)
    guide_title: str = Field(default="Практический гайд", min_length=1, max_length=32)

    support_username: str = "@support"
    terms_url: str = "https://telegram.org/tos/bot-developers"
    default_language: Literal["ru", "en", "es"] = "ru"
    timezone: str = "Europe/Moscow"

    port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"
    drop_pending_updates: bool = False
    broadcast_rate_per_second: int = Field(default=20, ge=1, le=25)
    database_pool_min_size: int = Field(default=2, ge=1, le=20)
    database_pool_max_size: int = Field(default=15, ge=2, le=100)

    @field_validator("admin_ids")
    @classmethod
    def validate_admin_ids(cls, value: list[int]) -> list[int]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("ADMIN_IDS must contain at least one Telegram user ID")
        return unique

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        return value.replace("postgres://", "postgresql://", 1)

    @field_validator("guide_download_url")
    @classmethod
    def validate_guide_download_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip().rstrip(",")
        if value.startswith("//"):
            value = f"https:{value}"
        elif value.startswith("drive.google.com/"):
            value = f"https://{value}"
        if not value.startswith(("https://", "http://")):
            raise ValueError("GUIDE_DOWNLOAD_URL must be an HTTP(S) URL")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        aliases = {
            "moscow": "Europe/Moscow",
            "msk": "Europe/Moscow",
        }
        value = aliases.get(value.strip().casefold(), value.strip())
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def validate_database_pool(self) -> Settings:
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError("DATABASE_POOL_MIN_SIZE cannot exceed DATABASE_POOL_MAX_SIZE")
        return self

    @property
    def admin_id_set(self) -> frozenset[int]:
        return frozenset(self.admin_ids)

    @property
    def guide_enabled(self) -> bool:
        return self.guide_download_url is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
