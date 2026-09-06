"""
Set a password directly, when nobody can get in.

    python scripts/reset_password.py --list
    python scripts/reset_password.py <username> <new password>

There is no self-service password reset anywhere in this product: no email is
ever sent, so there is no reset link to click. That is the right call for
studios who have a phone number and no email address, but it means a lost
password needs a hand on the server. This is that hand.

Run on the machine hosting the API. It also clears the recent failed sign-in
attempts for that account, because ten wrong guesses lock the identifier out
for fifteen minutes and a correct password would still be refused afterwards.

`must_change_password` is cleared rather than set. This gets used when someone
is locked out mid-event, and forcing them through a change screen while guests
are waiting is the wrong moment for it.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import system_session  # noqa: E402
from app.models import LoginAttempt, User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--list":
        with system_session() as db:
            rows = db.scalars(select(User).order_by(User.role)).all()
            if not rows:
                print("\n  no accounts at all. Run:  python scripts/create_admin.py admin\n")
                return 1
            print(f"\n  {'username':<24} {'role':<16} studio")
            for u in rows:
                print(f"  {u.username:<24} {u.role:<16} {u.studio_id or '-'}")
            print()
        return 0

    if len(argv) < 3:
        print(__doc__)
        return 2

    username = argv[1].strip().lower()
    password = argv[2]
    if len(password) < 8:
        print("  password must be at least 8 characters")
        return 2

    with system_session() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == username))
        if user is None:
            print(f"\n  no account called '{username}'. Try:  --list\n")
            return 1

        user.password_hash = hash_password(password)
        user.must_change_password = False
        user.status = "active"

        # Otherwise the lockout window outlives the reset.
        db.query(LoginAttempt).filter(LoginAttempt.identifier == username).delete()
        db.commit()

    print(f"\n  {username} can now sign in with the password you just set.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
