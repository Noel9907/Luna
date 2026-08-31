"""
Brings the database up to date.

    python scripts/init_db.py

This used to call `create_all`, which was right while nothing real was stored
and wrong the moment anything was. `create_all` only ever creates what is
missing: it will not add a column, will not change a type, and will not tell
you it did nothing. The first time you need to alter a table on a server
holding a wedding that already happened, it is no help at all.

So this now runs Alembic, which is the same thing production runs. Anything
that works here works there, and anything that breaks breaks in the same way.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import RLS_ACTIVE, system_engine  # noqa: E402


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        print("\n  migration failed. Nothing was changed.")
        return result.returncode

    with system_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version' ORDER BY tablename"
            )
        ).scalars().all()
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        policies = conn.execute(text("SELECT count(*) FROM pg_policies")).scalar_one()

    print(f"\n  schema at {version}, {len(tables)} tables, {policies} policies")
    for t in tables:
        print("   ", t)

    if not RLS_ACTIVE:
        print(
            "\n  DATABASE_APP_URL is not set, so the API will connect as the database\n"
            "  owner and row-level security will not apply. To turn it on, add this\n"
            "  to backend/.env:\n\n"
            f"    DATABASE_APP_URL=postgresql+psycopg://frame_app:{settings().app_db_password}"
            "@localhost:55432/frame\n\n"
            "  then check it with:  python scripts/prove_isolation.py"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
