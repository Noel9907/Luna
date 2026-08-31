"""
Creates a usable set of accounts for local development.

    python scripts/seed_dev.py

Because accounts are created by hand everywhere in this system, a fresh
database has nobody in it and nothing can be tested. This makes the smallest
set that lets you actually use the apps, with known passwords and
`must_change_password` already cleared so you are not forced through that
screen on every reset.

Refuses to run when ENV=production, where those two facts would be a hole
rather than a convenience.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import system_session  # noqa: E402
from app.models import Studio, User  # noqa: E402
from app.security import hash_password  # noqa: E402

ADMIN_USER = "admin"
ADMIN_PASSWORD = "dev-admin-password"

OWNER_USER = "lakeview"
OWNER_PASSWORD = "dev-owner-password"

SHOOTER_USER = "shooter"
SHOOTER_PASSWORD = "dev-shooter-password"

STUDIO_NAME = "Lakeview Studio"
STUDIO_SLUG = "lakeview"


def upsert_user(db, username, password, name, role, studio_id) -> User:
    user = db.scalar(select(User).where(func.lower(User.username) == username))
    if user is None:
        user = User(username=username, studio_id=studio_id, name=name, role=role, password_hash="")
        db.add(user)
    user.password_hash = hash_password(password)
    user.studio_id = studio_id
    user.role = role
    user.status = "active"
    user.must_change_password = False
    return user


def main() -> int:
    if settings().env == "production":
        print("  refusing: seed_dev creates accounts with known passwords.")
        return 1

    with system_session() as db:
        studio = db.scalar(select(Studio).where(Studio.slug == STUDIO_SLUG))
        if studio is None:
            studio = Studio(
                name=STUDIO_NAME,
                slug=STUDIO_SLUG,
                phone="+91 98470 12345",
                default_face_retention_days=settings().default_face_retention_days,
                status="active",
            )
            db.add(studio)
            db.flush()

        upsert_user(db, ADMIN_USER, ADMIN_PASSWORD, "Platform Admin", "platform_admin", None)
        upsert_user(db, OWNER_USER, OWNER_PASSWORD, "Studio Owner", "owner", studio.id)
        upsert_user(db, SHOOTER_USER, SHOOTER_PASSWORD, "Second Shooter", "photographer", studio.id)
        db.commit()

        print(f"\n  studio  {studio.name}  ({studio.id})")
        print(f"  face retention  {studio.default_face_retention_days} days\n")

    for label, user, password in (
        ("platform admin", ADMIN_USER, ADMIN_PASSWORD),
        ("studio owner  ", OWNER_USER, OWNER_PASSWORD),
        ("photographer  ", SHOOTER_USER, SHOOTER_PASSWORD),
    ):
        print(f"  {label}  {user:<14} {password}")
    print("\n  Development only. These passwords are in the repository.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
