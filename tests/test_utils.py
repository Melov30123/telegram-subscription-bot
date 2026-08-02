from datetime import UTC, datetime

import pytest

from subscription_bot.utils import format_datetime, parse_int, safe


def test_safe_escapes_html() -> None:
    assert safe("<script>") == "&lt;script&gt;"
    assert safe(None) == "—"


def test_parse_int_range() -> None:
    assert parse_int("30", minimum=1, maximum=365, name="days") == 30
    with pytest.raises(ValueError):
        parse_int("0", minimum=1, maximum=365, name="days")


def test_format_datetime_converts_timezone() -> None:
    value = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert format_datetime(value, "Europe/Moscow") == "01.01.2026 03:00"
