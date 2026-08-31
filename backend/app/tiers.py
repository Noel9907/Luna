"""
What a studio can buy.

These are the catalogue, not the record. The moment an event is paid for, every
value below is copied onto the event row and nothing ever reads back through
here to decide what that event is entitled to. Raising a price or shortening a
retention window next year must not silently rewrite what somebody already
bought, and a foreign key to a tiers table does exactly that.

Face retention is deliberately absent. It is not a tier feature and cannot be
bought: it comes from the studio's own agreement, capped by
MAX_FACE_RETENTION_DAYS, and is snapshotted onto the event alongside these.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Tier:
    code: str
    name: str
    price_paise: int
    branding_mode: str  # platform | studio
    photo_retention_days: int
    custom_domain: bool


TIERS: tuple[Tier, ...] = (
    Tier("basic", "Basic", 150_000, "platform", 30, False),
    Tier("pro", "Pro", 300_000, "studio", 90, False),
    Tier("premium", "Premium", 600_000, "studio", 180, True),
)

BY_CODE: dict[str, Tier] = {t.code: t for t in TIERS}
DEFAULT_TIER = BY_CODE["pro"]


def get(code: str) -> Tier | None:
    return BY_CODE.get(code)


def as_json(tier: Tier, face_retention_days: int) -> dict:
    """
    The shape the tier picker renders.

    Face retention is passed in rather than read from the tier, because it
    belongs to the studio, and showing a number the studio did not negotiate
    would be a promise nobody made.
    """
    return {**asdict(tier), "face_retention_days": face_retention_days}
