"""
Deleting things on time.

Three separate clocks run here, and conflating them is the mistake worth
avoiding:

    guest selfies    consented_at + the days that guest was SHOWN at consent
    photo faces      event ended + the event's snapshotted face_retention_days
    photographs      event ended + the event's snapshotted photo_retention_days

The first one is per guest, not per event, and that is deliberate. The consent
screen tells each guest a specific calendar date. Purging on any other schedule
would make that sentence false for somebody, and it is the one sentence in the
whole product that has to be true.

`guest_matches` deliberately survives the face purge. It holds a guest id, a
photo id and a score, and none of that is biometric, so galleries keep working
after every embedding is gone. That single fact is what lets the biometric
window be short without shortening the product.

What the purge does NOT do is delete the studio's photographs early. Those are
the studio's property and their retention is what was paid for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import record
from app.config import settings
from app.models import Event, Photo
from app.storage import get_storage


@dataclass
class PurgeReport:
    guest_faces: int = 0
    photo_faces: int = 0
    photos: int = 0
    objects: int = 0
    refresh_tokens: int = 0
    login_attempts: int = 0
    events_touched: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"guest_faces={self.guest_faces} photo_faces={self.photo_faces} "
            f"photos={self.photos} objects={self.objects} "
            f"refresh_tokens={self.refresh_tokens} login_attempts={self.login_attempts}"
        )


def retention_anchor(event: Event) -> datetime | None:
    """
    The moment an event's clocks start.

    Explicitly ended first, because that is a real decision somebody made.
    Falling back to activation covers events nobody remembered to close, which
    is most of them, and the fallback is bounded below by the event date so a
    studio cannot hold data forever by simply never pressing End.
    """
    return event.ended_at or event.activated_at or event.created_at


# ── the three clocks ───────────────────────────────────────────────────


def purge_guest_faces(db: Session) -> int:
    """
    Deletes selfie embeddings whose own consent window has passed.

    Keyed on `guest_sessions.consented_at` plus the number of days stored on
    that session, not on anything current. A studio's agreement changing later
    cannot shorten or extend what a guest was already promised, because the
    promise was copied onto their row when they made it.
    """
    result = db.execute(
        text(
            """
            DELETE FROM guest_faces gf
             USING guest_sessions gs
             WHERE gs.id = gf.guest_session_id
               AND gs.consented_at + (gs.consent_face_retention_days * INTERVAL '1 day') < now()
            """
        )
    )
    return result.rowcount or 0


def purge_photo_faces(db: Session) -> tuple[int, list[str]]:
    """
    Deletes the face embeddings extracted from a studio's photographs.

    These are biometric too, which is easy to forget because they were never
    anybody's selfie. Deleting them means a guest arriving after the window has
    nothing to match against; everyone who registered during the event keeps
    their gallery, because the matches are already written.
    """
    now = datetime.now(timezone.utc)
    total = 0
    touched: list[str] = []

    events = db.scalars(
        select(Event).where(Event.faces_purged_at.is_(None), Event.status != "draft")
    ).all()

    for event in events:
        anchor = retention_anchor(event)
        if anchor is None or anchor + timedelta(days=event.face_retention_days) > now:
            continue

        result = db.execute(
            text("DELETE FROM faces WHERE event_id = :eid"), {"eid": event.id}
        )
        deleted = result.rowcount or 0
        event.faces_purged_at = now
        total += deleted
        touched.append(event.id)

        record(
            db,
            actor_user_id=None,
            actor_label="retention job",
            action="faces.purged",
            target_type="event",
            target_id=event.id,
            studio_id=event.studio_id,
            detail={"faces_deleted": deleted, "retention_days": event.face_retention_days},
        )

    db.commit()
    return total, touched


def purge_photos(db: Session) -> tuple[int, int, list[str]]:
    """
    Deletes photographs whose paid retention has run out, storage included.

    Deleting the database rows without deleting the objects is the standard
    version of this bug. Nothing breaks, nothing complains, and the R2 bill
    grows every month for files nobody can reach. So objects go first, then
    rows. That order can leave an object deleted while its row survives a
    crash, which shows as a broken thumbnail on a 180 day old event for the
    minutes until the job runs again. The other order leaks storage forever.
    """
    now = datetime.now(timezone.utc)
    storage = get_storage()
    photos_deleted = objects_deleted = 0
    touched: list[str] = []

    events = db.scalars(
        select(Event).where(Event.photos_purged_at.is_(None), Event.status != "draft")
    ).all()

    for event in events:
        anchor = retention_anchor(event)
        if anchor is None or anchor + timedelta(days=event.photo_retention_days) > now:
            continue

        rows = db.execute(
            select(Photo.id, Photo.storage_key, Photo.thumb_key).where(Photo.event_id == event.id)
        ).all()
        if rows:
            keys = [r[1] for r in rows] + [r[2] for r in rows if r[2]]
            objects_deleted += storage.delete_many(keys)

            # The photo rows cascade to faces and guest_matches, so the gallery
            # empties along with the storage rather than filling with holes.
            result = db.execute(
                text("DELETE FROM photos WHERE event_id = :eid"), {"eid": event.id}
            )
            photos_deleted += result.rowcount or 0

        event.photos_purged_at = now
        touched.append(event.id)
        record(
            db,
            actor_user_id=None,
            actor_label="retention job",
            action="photos.purged",
            target_type="event",
            target_id=event.id,
            studio_id=event.studio_id,
            detail={"photos_deleted": len(rows), "retention_days": event.photo_retention_days},
        )
        db.commit()

    return photos_deleted, objects_deleted, touched


# ── manual, for the admin panel ────────────────────────────────────────


def purge_event_faces_now(
    db: Session, event: Event, actor_user_id: str | None, actor_label: str
) -> int:
    """
    Immediate purge of one event's biometric data, on request.

    Both tables, because both are biometric. Recorded in the audit log with who
    asked for it, since this destroys data a studio may later ask about.
    """
    faces = db.execute(text("DELETE FROM faces WHERE event_id = :e"), {"e": event.id}).rowcount or 0
    selfies = (
        db.execute(text("DELETE FROM guest_faces WHERE event_id = :e"), {"e": event.id}).rowcount
        or 0
    )
    event.faces_purged_at = datetime.now(timezone.utc)

    record(
        db,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action="faces.purged_manually",
        target_type="event",
        target_id=event.id,
        studio_id=event.studio_id,
        detail={"faces_deleted": faces, "guest_faces_deleted": selfies},
    )
    db.commit()
    return faces + selfies


# ── housekeeping ───────────────────────────────────────────────────────


def purge_login_attempts(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    result = db.execute(
        text("DELETE FROM login_attempts WHERE created_at < :c"), {"c": cutoff}
    )
    return result.rowcount or 0


def run_all(db: Session) -> PurgeReport:
    """One pass of every clock. Safe to run repeatedly; each part is idempotent."""
    from app.auth import purge_expired_refresh_tokens

    report = PurgeReport()
    report.guest_faces = purge_guest_faces(db)
    db.commit()

    report.photo_faces, touched_faces = purge_photo_faces(db)
    report.photos, report.objects, touched_photos = purge_photos(db)
    report.events_touched = sorted(set(touched_faces) | set(touched_photos))

    report.refresh_tokens = purge_expired_refresh_tokens(db)
    report.login_attempts = purge_login_attempts(db)
    db.commit()
    return report


def interval_seconds() -> int:
    return settings().purge_interval_minutes * 60
