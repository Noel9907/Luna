"""
Proves that one studio cannot see or touch another studio's data.

Run it after any migration that adds a table, and before any deploy:

    python scripts/prove_isolation.py

The value of row-level security is entirely in whether it actually holds. A
policy that exists but is bypassed, or a table somebody added last week and
forgot to enable, looks exactly like a working one until the Saturday two
studios are busy at the same time. This script is what turns "we have RLS" into
something checked rather than believed.

Four things are checked, and the last two are the ones people get wrong:

    1. reads are filtered            studio A does not see studio B's event
    2. writes are filtered           A cannot create a row stamped B
    3. no tenant means no rows       an unset connection sees nothing, rather
                                     than everything
    4. no table is unprotected       every table with a studio_id has a policy
"""

from __future__ import annotations

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402

PASS = "  ok   "
FAIL = "  FAIL "


def app_url() -> str:
    s = settings()
    if s.database_app_url:
        return s.database_app_url
    # Derive it from the owner URL so this runs on a fresh clone.
    owner = s.database_url
    tail = owner.split("@", 1)[1]
    return f"postgresql+psycopg://frame_app:{s.app_db_password}@{tail}"


def main() -> int:
    owner_engine = create_engine(settings().database_url, future=True)
    app_engine = create_engine(app_url(), future=True)

    a_studio, b_studio = str(uuid.uuid4()), str(uuid.uuid4())
    a_event, b_event = str(uuid.uuid4()), str(uuid.uuid4())
    marker = uuid.uuid4().hex[:8]
    failures = 0

    # ── set up two studios as the owner ────────────────────────────────
    with owner_engine.begin() as c:
        for sid, name in ((a_studio, "A"), (b_studio, "B")):
            c.execute(
                text(
                    "INSERT INTO studios (id, name, slug, default_face_retention_days, "
                    "status, created_at) VALUES (:id, :n, :slug, 30, 'active', now())"
                ),
                {"id": sid, "n": f"Studio {name} {marker}", "slug": f"{name.lower()}-{marker}"},
            )
        for eid, sid, name in ((a_event, a_studio, "A"), (b_event, b_studio, "B")):
            c.execute(
                text(
                    "INSERT INTO events (id, studio_id, name, event_date, status, qr_token, "
                    "tier_code, price_paise, branding_mode, photo_retention_days, "
                    "face_retention_days, created_at) "
                    "VALUES (:id, :sid, :n, '2026-09-01', 'active', :qr, 'pro', 300000, "
                    "'studio', 90, 30, now())"
                ),
                {"id": eid, "sid": sid, "n": f"Wedding {name}", "qr": f"{name}{marker}"},
            )

    try:
        # ── 1. reads are filtered ──────────────────────────────────────
        with app_engine.connect() as c:
            c.execute(text("SELECT set_config('app.studio_id', :s, false)"), {"s": a_studio})
            # str() matters: a raw query returns uuid.UUID objects, not the
            # strings the ORM hands back, and comparing the two is always false.
            seen = {str(r[0]) for r in c.execute(text("SELECT id FROM events")).all()}

        if a_event in seen and b_event not in seen:
            print(f"{PASS} studio A sees its own event and not studio B's")
        else:
            failures += 1
            print(f"{FAIL} studio A saw {len(seen)} events; expected exactly its own")

        # ── 2. writes are filtered ─────────────────────────────────────
        # The interesting half. A policy with USING but no WITH CHECK stops A
        # reading B's rows while still letting A create rows labelled B.
        blocked = False
        try:
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.studio_id', :s, true)"), {"s": a_studio})
                c.execute(
                    text(
                        "INSERT INTO events (id, studio_id, name, event_date, status, qr_token, "
                        "tier_code, price_paise, branding_mode, photo_retention_days, "
                        "face_retention_days, created_at) "
                        "VALUES (:id, :sid, 'smuggled', '2026-09-01', 'active', :qr, 'pro', "
                        "300000, 'studio', 90, 30, now())"
                    ),
                    {"id": str(uuid.uuid4()), "sid": b_studio, "qr": f"X{marker}"},
                )
        except Exception:
            blocked = True

        if blocked:
            print(f"{PASS} studio A cannot insert a row stamped with studio B")
        else:
            failures += 1
            print(f"{FAIL} studio A wrote a row into studio B: WITH CHECK is missing")

        # ── 3. an unpinned connection sees nothing ─────────────────────
        with app_engine.connect() as c:
            count = c.execute(text("SELECT count(*) FROM events")).scalar_one()
        if count == 0:
            print(f"{PASS} a connection with no tenant set sees no rows")
        else:
            failures += 1
            print(f"{FAIL} a connection with no tenant set saw {count} events: fails open")

        # ── 4. every tenant table is actually protected ────────────────
        with owner_engine.connect() as c:
            unprotected = c.execute(
                text(
                    """
                    SELECT c.relname
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                      JOIN information_schema.columns col
                        ON col.table_name = c.relname AND col.table_schema = n.nspname
                     WHERE n.nspname = 'public'
                       AND c.relkind = 'r'
                       AND col.column_name = 'studio_id'
                       AND c.relrowsecurity = false
                     GROUP BY c.relname
                     ORDER BY c.relname
                    """
                )
            ).scalars().all()

        # audit_log carries a studio_id but is never reachable from the
        # application role, so it is exempt by design rather than by omission.
        unprotected = [t for t in unprotected if t != "audit_log"]
        if not unprotected:
            print(f"{PASS} every table with a studio_id has row-level security enabled")
        else:
            failures += 1
            print(f"{FAIL} no row-level security on: {', '.join(unprotected)}")

    finally:
        with owner_engine.begin() as c:
            c.execute(
                text("DELETE FROM studios WHERE id = ANY(:ids)"),
                {"ids": [a_studio, b_studio]},
            )

    print()
    if failures:
        print(f"  {failures} check(s) failed. Do not deploy this.")
        return 1
    print("  tenant isolation holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
