import uuid

import pytest

from subscription_bot.utils import parse_payment_payload


def test_parse_payment_payload() -> None:
    value = uuid.uuid4()
    assert parse_payment_payload(f"sub:{value}") == value


@pytest.mark.parametrize("payload", ["bad", "other:123", "sub:not-a-uuid"])
def test_reject_bad_payment_payload(payload: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        parse_payment_payload(payload)
