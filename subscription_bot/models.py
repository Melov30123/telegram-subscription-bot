from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PaymentCompletion:
    payment_id: int
    access_until: datetime
    already_processed: bool = False


@dataclass(frozen=True, slots=True)
class PromoRedemption:
    ok: bool
    message: str
    access_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class Stats:
    users: int
    active: int
    expired: int
    blocked_bot: int
    payments: int
    stars_total: int
    stars_30d: int
    new_users_24h: int
