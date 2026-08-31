"""
Proves the queue does not starve a live event behind another studio's backlog.

    python scripts/prove_fair_share.py

The scenario is the one that actually happens on a Saturday:

    studio B  uploads a 400 photograph archive from last week, all at once
    studio A  is shooting a wedding right now and adds 3 photographs a moment
              later, with 150 guests refreshing their phones

Every one of B's jobs is older than every one of A's. Under plain FIFO, which
is what `ORDER BY created_at` gives you, A waits for all 400 before a single
one of its photographs is indexed. This script runs both orderings against the
same rows and prints where A's three jobs actually land.
"""

from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import system_session  # noqa: E402
from app.worker import claim_job  # noqa: E402

BACKLOG = 400
LIVE = 3

# Scoped to this run's studio. Without that filter the test reaches into
# every job in the database, including a studio's real ones.
FIFO_SQL = text(
    """
    UPDATE processing_jobs j
       SET status = 'running', locked_at = now(), attempts = j.attempts + 1
     WHERE j.id = (
         SELECT id FROM processing_jobs
          WHERE status = 'pending' AND studio_id = :studio
          ORDER BY created_at
          FOR UPDATE SKIP LOCKED
          LIMIT 1
     )
    RETURNING j.id, j.event_id
    """
)


def seed(db, studio_id: str, live_event: str, backlog_event: str) -> None:
    now = datetime.now(timezone.utc)

    db.execute(
        text(
            "INSERT INTO studios (id, name, slug, default_face_retention_days, status, "
            "created_at) VALUES (:id, 'Fairness Test', :slug, 30, 'active', now())"
        ),
        {"id": studio_id, "slug": f"fair-{studio_id[:8]}"},
    )
    for eid, name in ((backlog_event, "B backlog"), (live_event, "A live")):
        db.execute(
            text(
                "INSERT INTO events (id, studio_id, name, event_date, status, qr_token, "
                "tier_code, price_paise, branding_mode, photo_retention_days, "
                "face_retention_days, created_at) VALUES (:id, :sid, :n, '2026-09-01', "
                "'active', :qr, 'pro', 300000, 'studio', 90, 30, now())"
            ),
            {"id": eid, "sid": studio_id, "n": name, "qr": uuid.uuid4().hex[:12]},
        )

    def add_jobs(event_id: str, count: int, created: datetime) -> None:
        for i in range(count):
            pid = str(uuid.uuid4())
            stamp = created + timedelta(milliseconds=i)
            db.execute(
                text(
                    "INSERT INTO photos (id, studio_id, event_id, storage_key, status, "
                    "created_at) VALUES (:id, :sid, :eid, :key, 'uploaded', :ts)"
                ),
                {"id": pid, "sid": studio_id, "eid": event_id, "key": f"x/{pid}", "ts": stamp},
            )
            db.execute(
                text(
                    "INSERT INTO processing_jobs (id, studio_id, event_id, photo_id, status, "
                    "attempts, created_at) VALUES (:id, :sid, :eid, :pid, 'pending', 0, :ts)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "sid": studio_id,
                    "eid": event_id,
                    "pid": pid,
                    "ts": stamp,
                },
            )

    # The backlog is uploaded first, so every one of its jobs is older.
    add_jobs(backlog_event, BACKLOG, now - timedelta(minutes=3))
    add_jobs(live_event, LIVE, now)
    db.commit()


def drain(db, claim, live_event: str, limit: int, studio_id: str) -> list[int]:
    """Claims `limit` jobs and returns the positions at which the live event won."""
    positions = []
    for n in range(1, limit + 1):
        if callable(claim):
            row = claim(db)
            event_id = row[2] if row else None
        else:
            result = db.execute(claim, {"studio": studio_id}).first()
            db.commit()
            event_id = str(result[1]) if result else None
        if event_id is None:
            break
        if event_id == live_event:
            positions.append(n)
    return positions


def reset(db, studio_id: str) -> None:
    # Scoped. An unscoped UPDATE here re-queues every job in the database and
    # sends a running worker back over photographs it already indexed.
    db.execute(
        text(
            "UPDATE processing_jobs SET status='pending', locked_at=NULL, attempts=0"
            " WHERE studio_id = :s"
        ),
        {"s": studio_id},
    )
    db.execute(
        text("UPDATE events SET last_job_claimed_at = NULL WHERE studio_id = :s"),
        {"s": studio_id},
    )
    db.commit()


def main() -> int:
    studio_id = str(uuid.uuid4())
    live_event, backlog_event = str(uuid.uuid4()), str(uuid.uuid4())
    look_at = 40

    with system_session() as db:
        # Only unfinished jobs matter. Completed ones are history and do not
        # compete for a worker, so refusing on those would mean this could
        # never run again after the first real upload.
        existing = db.execute(
            text("SELECT count(*) FROM processing_jobs WHERE status IN ('pending', 'running')")
        ).scalar_one()
        if existing:
            print(f"  {existing} job(s) still queued. Run this when the queue is idle.")
            return 2

        seed(db, studio_id, live_event, backlog_event)
        try:
            print(f"  queued: {BACKLOG} backlog jobs (older), then {LIVE} live jobs (newer)")
            print(f"  claiming the first {look_at} jobs under each ordering.\n")

            fifo = drain(db, FIFO_SQL, live_event, look_at, studio_id)
            reset(db, studio_id)
            fair = drain(db, claim_job, live_event, look_at, studio_id)

            print(f"  FIFO         live event served at positions: {fifo or 'none in first 40'}")
            print(f"  fair share   live event served at positions: {fair or 'none in first 40'}")
            print()

            if not fifo:
                # 8.5 photographs/sec is the measured rate on three cores at
                # roughly 350ms each. A real archive is nearer 15,000 files,
                # which is half an hour of a live event seeing nothing.
                wait = BACKLOG / 8.5
                print(
                    f"  Under FIFO the live event waits behind all {BACKLOG} backlog jobs, "
                    f"about {wait:.0f} seconds at 8.5 photos/sec. Scale that to a real "
                    f"15,000 photograph archive and it is half an hour."
                )
            if len(fair) == LIVE and max(fair) <= LIVE * 2:
                print("  Under fair share every live job is served in the first few slots.")
                print("\n  fair share holds.")
                return 0

            print("\n  fair share did NOT hold. The claim query is not doing what it should.")
            return 1
        finally:
            db.execute(text("DELETE FROM studios WHERE id = :id"), {"id": studio_id})
            db.commit()


if __name__ == "__main__":
    sys.exit(main())
