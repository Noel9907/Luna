"""
Creates a platform administrator. Run this once, on a new deployment.

    python scripts/create_admin.py noel

There is no self-signup anywhere in this system, which means there has to be
exactly one account that a script creates rather than a person. This is it.
Everything else follows from here: an admin creates a studio and its owner, and
that owner creates their photographers.

A platform admin has `studio_id` NULL. That null is not a placeholder, it is
the mechanism: the row-level security policies compare a row's studio to the
connection's pinned studio, so an account with no studio matches nothing on the
tenant connection and is served entirely through the owner connection instead.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import system_session  # noqa: E402
from app.models import User  # noqa: E402
from app.routers.studio import generate_password  # noqa: E402
from app.security import hash_password  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python scripts/create_admin.py <username> [name]")
        return 2

    username = argv[1].strip().lower()
    name = argv[2] if len(argv) > 2 else username

    with system_session() as db:
        clash = db.scalar(select(User).where(func.lower(User.username) == username))
        if clash is not None:
            print(f"  '{username}' already exists.")
            return 1

        password = generate_password()
        db.add(
            User(
                studio_id=None,
                username=username,
                name=name,
                password_hash=hash_password(password),
                role="platform_admin",
                status="active",
                # Set even for an admin. This password was printed to a
                # terminal and is now in a scrollback buffer somewhere.
                must_change_password=True,
            )
        )
        db.commit()

    print("\n  platform admin created\n")
    print(f"    username  {username}")
    print(f"    password  {password}\n")
    print("  Shown once. Change it at first sign-in.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
