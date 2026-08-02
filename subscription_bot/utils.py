from __future__ import annotations

import html
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def format_datetime(value: datetime | None, timezone: str = "Europe/Moscow") -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y %H:%M")


def days_remaining(value: datetime) -> str:
    seconds = max(0, int((value - datetime.now(UTC)).total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours = remainder // 3600
    if days:
        return f"{days} дн. {hours} ч."
    return f"{hours} ч."


def safe(value: object | None) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def parse_int(value: str, *, minimum: int, maximum: int, name: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} должно быть целым числом") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name}: допустимо от {minimum} до {maximum}")
    return number


def parse_payment_payload(payload: str) -> uuid.UUID:
    prefix, value = payload.split(":", 1)
    if prefix != "sub":
        raise ValueError("Unknown invoice payload")
    return uuid.UUID(value)
