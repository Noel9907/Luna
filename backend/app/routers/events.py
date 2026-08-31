"""
Events: create, list, watch, end.

Every route here takes `studio_db`, so the connection is already pinned to the
caller's studio and a missing `WHERE studio_id = ...` returns nothing rather
than somebody else's wedding. The queries below still read naturally because
the tenant filter is applied underneath them by the database.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, tiers
from app.config import settings
from app.errors import ApiError
from app.models import Event, GuestSession, Job, Photo, Studio
from app.security import Principal
from app.storage import get_storage

router = APIRouter(tags=["events"])


def event_json(e: Event) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "event_date": e.event_date,
        "status": e.status,
        "tier_code": e.tier_code,
        "branding_mode": e.branding_mode,
        "photo_retention_days": e.photo_retention_days,
        "face_retention_days": e.face_retention_days,
        "qr_url": f"{settings().guest_base_url.rstrip('/')}/g/{e.qr_token}",
        "created_at": e.created_at.isoformat(),
        "activated_at": e.activated_at.isoformat() if e.activated_at else None,
        "ended_at": e.ended_at.isoformat() if e.ended_at else None,
    }


def load_event(db: Session, event_id: str) -> Event:
    """
    Fetches an event, or 404s.

    No studio check here on purpose. The session is pinned, so an event
    belonging to another studio is not merely rejected, it is not visible at
    all, and `db.get` returns None exactly as it would for an id that never
    existed. That is the correct answer to give: confirming that an id exists
    but belongs to someone else is itself a small leak.
    """
    e = db.get(Event, event_id)
    if e is None:
        raise ApiError(404, "NOT_FOUND", "That event does not exist.")
    return e


class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    event_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    tier_code: str = "pro"


@router.get("/events")
def list_events(
    status: str | None = Query(default=None, pattern="^(draft|active|ended)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(auth.active_studio_db),
):
    q = select(Event).order_by(Event.created_at.desc()).limit(limit)
    if status:
        q = q.where(Event.status == status)
    rows = db.scalars(q).all()
    return {"items": [event_json(e) for e in rows], "next_cursor": None}


@router.post("/events", status_code=201)
def create_event(
    body: EventIn,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.active_studio_db),
):
    """
    Creates an event in `draft`.

    Draft means no guest can reach it and no photograph can be uploaded to it.
    Only the payment webhook moves it to `active`. The browser coming back from
    Razorpay never does, because browsers get closed and phones lose signal on
    the confirmation screen, and an event that only activates when the customer
    happens to keep a tab open is an event that sometimes does not activate at
    a wedding.
    """
    tier = tiers.get(body.tier_code)
    if tier is None:
        raise ApiError(422, "UNKNOWN_TIER", "That plan does not exist.")

    studio = db.get(Studio, who.studio_id)
    if studio is None:
        raise ApiError(404, "NOT_FOUND", "Studio not found.")

    # Snapshot everything now. From here on this event's entitlements are a
    # property of the row, not of the current price list or the studio's
    # current agreement.
    e = Event(
        studio_id=who.studio_id,
        name=body.name,
        event_date=body.event_date,
        status="draft",
        qr_token=secrets.token_urlsafe(12)[:16],
        tier_code=tier.code,
        price_paise=tier.price_paise,
        branding_mode=tier.branding_mode,
        photo_retention_days=tier.photo_retention_days,
        face_retention_days=min(
            studio.default_face_retention_days, settings().max_face_retention_days
        ),
    )
    db.add(e)
    db.commit()
    return event_json(e)


@router.get("/events/{event_id}")
def get_event(event_id: str, db: Session = Depends(auth.active_studio_db)):
    return event_json(load_event(db, event_id))


@router.post("/events/{event_id}/end")
def end_event(event_id: str, db: Session = Depends(auth.active_studio_db)):
    """
    Closes an event to new guests.

    Photographs already indexed stay reachable for the retention window the
    studio bought; this only stops new selfies. It also starts the clock that
    the face purge measures from, which is why it is an explicit action rather
    than something inferred from the date.
    """
    e = load_event(db, event_id)
    if e.status == "ended":
        return event_json(e)
    if e.status != "active":
        raise ApiError(409, "NOT_ACTIVE", "That event was never activated.")
    e.status = "ended"
    e.ended_at = datetime.now(timezone.utc)
    db.commit()
    return event_json(e)


@router.get("/events/{event_id}/stats")
def event_stats(event_id: str, db: Session = Depends(auth.active_studio_db)):
    """
    The live event screen polls this every few seconds.

    Three counts and one age, all indexed, deliberately cheap: during a wedding
    this is the single most frequently called endpoint the studio app has.
    """
    load_event(db, event_id)

    counts = dict(
        db.execute(
            select(Photo.status, func.count())
            .where(Photo.event_id == event_id)
            .group_by(Photo.status)
        ).all()
    )
    guests = db.scalar(
        select(func.count()).select_from(GuestSession).where(GuestSession.event_id == event_id)
    )
    oldest = db.scalar(
        select(func.min(Job.created_at)).where(
            Job.event_id == event_id, Job.status.in_(("pending", "running"))
        )
    )
    return {
        "pending": counts.get("pending", 0),
        "uploaded": counts.get("uploaded", 0),
        "processing": counts.get("processing", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "guests_registered": guests or 0,
        # Climbing means the queue is falling behind. This is the earliest
        # visible sign of trouble, well before any guest notices a gallery that
        # has stopped filling.
        "oldest_pending_seconds": (
            int((datetime.now(timezone.utc) - oldest).total_seconds()) if oldest else None
        ),
    }


@router.get("/events/{event_id}/photos")
def list_photos(
    event_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    before: str | None = Query(default=None),
    db: Session = Depends(auth.active_studio_db),
):
    """
    The studio's own view of what it uploaded.

    Keyset paginated on `created_at` rather than OFFSET. A studio scrolling
    back through 4,000 photographs with OFFSET makes the database count and
    discard every row it skips, so page 40 costs forty times page one. Passing
    the timestamp of the last row seen costs the same at any depth.
    """
    load_event(db, event_id)
    storage = get_storage()

    q = select(Photo).where(Photo.event_id == event_id).order_by(Photo.created_at.desc())
    if status:
        q = q.where(Photo.status == status)
    if before:
        q = q.where(Photo.created_at < datetime.fromisoformat(before))
    rows = db.scalars(q.limit(limit)).all()

    return {
        "items": [
            {
                "id": p.id,
                "status": p.status,
                "thumbnail_url": (
                    storage.signed_get_url(p.thumb_key or p.storage_key) if p.thumb_key else None
                ),
                "face_count": p.face_count,
                "error_code": p.error_code,
                "created_at": p.created_at.isoformat(),
                "processed_at": p.processed_at.isoformat() if p.processed_at else None,
                "filename": p.filename,
                "size_bytes": p.size_bytes,
            }
            for p in rows
        ],
        "next_cursor": rows[-1].created_at.isoformat() if len(rows) == limit else None,
    }


@router.get("/tiers")
def list_tiers(
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.active_studio_db),
):
    """
    The tier picker.

    Face retention comes from the studio's own agreement rather than the tier,
    so the number shown here is the number that event will actually get.
    """
    studio = db.get(Studio, who.studio_id)
    days = min(
        studio.default_face_retention_days if studio else settings().default_face_retention_days,
        settings().max_face_retention_days,
    )
    return {"items": [tiers.as_json(t, days) for t in tiers.TIERS]}
