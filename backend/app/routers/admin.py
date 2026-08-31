"""
The platform admin panel. Two people use this.

Every route runs on the owner connection, because an admin is not a tenant:
seeing across studios is the entire job. That is why this is a separate
dependency and a separate connection rather than a role check bolted onto the
tenant path, where it would have meant writing a policy that lets one role see
everything and hoping nothing else ever matches it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth, retention
from app.audit import record
from app.config import settings
from app.errors import ApiError
from app.models import Event, Face, GuestSession, Payment, Photo, Studio, User
from app.routers.studio import generate_password, member_json
from app.security import Principal, hash_password

router = APIRouter(tags=["admin"], prefix="/admin")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:50] or "studio"


def month_start() -> datetime:
    """
    Midnight on the first of the current month, UTC.

    Good enough for a dashboard. India is UTC+5:30, so for five and a half
    hours on the first of each month this counts slightly differently from what
    a person in Kerala would call "this month". Not worth a timezone library
    for a number nobody invoices from.
    """
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def studio_json(s: Studio, stats: dict | None = None) -> dict:
    """
    One studio, in the shape the admin table renders.

    Field names follow the frontend's `AdminStudio` rather than the column
    names, because this endpoint exists to fill that table and nothing else
    consumes it.
    """
    return {
        "id": s.id,
        "name": s.name,
        "slug": s.slug,
        "city": s.city,
        "phone": s.phone,
        "status": s.status,
        "brand_color": s.brand_color,
        "default_face_retention_days": s.default_face_retention_days,
        "created_at": s.created_at.isoformat(),
        "owner_username": "",
        "events_total": 0,
        "events_this_month": 0,
        "photos_total": 0,
        "revenue_paise": 0,
        "last_event_at": None,
        **(stats or {}),
    }


def _owner_of(db: Session, studio_id: str) -> User | None:
    """
    The account to act on when the admin says "this studio's owner".

    A studio can have more than one owner. The oldest is the one the platform
    created alongside the studio, which is the one whose password an admin is
    resetting after a phone call.
    """
    return db.scalar(
        select(User)
        .where(User.studio_id == studio_id, User.role == "owner", User.status == "active")
        .order_by(User.created_at)
        .limit(1)
    )


# ── studios ────────────────────────────────────────────────────────────


@router.get("/studios")
def list_studios(db: Session = Depends(auth.admin_db)):
    """
    Every studio with the numbers the table shows.

    Five grouped aggregates rather than a query per studio. With one studio the
    difference is nothing; the point is that it stays one round trip at fifty.
    """
    since = month_start()

    events_total = dict(
        db.execute(select(Event.studio_id, func.count()).group_by(Event.studio_id)).all()
    )
    events_month = dict(
        db.execute(
            select(Event.studio_id, func.count())
            .where(Event.created_at >= since)
            .group_by(Event.studio_id)
        ).all()
    )
    photos_total = dict(
        db.execute(select(Photo.studio_id, func.count()).group_by(Photo.studio_id)).all()
    )
    revenue = dict(
        db.execute(
            select(Payment.studio_id, func.coalesce(func.sum(Payment.amount_paise), 0))
            .where(Payment.status == "captured")
            .group_by(Payment.studio_id)
        ).all()
    )
    last_event = dict(
        db.execute(
            select(Event.studio_id, func.max(Event.created_at)).group_by(Event.studio_id)
        ).all()
    )
    owners = dict(
        db.execute(
            select(User.studio_id, func.min(User.username))
            .where(User.role == "owner")
            .group_by(User.studio_id)
        ).all()
    )

    rows = db.scalars(select(Studio).order_by(Studio.created_at.desc())).all()
    return {
        "items": [
            studio_json(
                s,
                {
                    "owner_username": owners.get(s.id) or "",
                    "events_total": events_total.get(s.id, 0),
                    "events_this_month": events_month.get(s.id, 0),
                    "photos_total": photos_total.get(s.id, 0),
                    "revenue_paise": int(revenue.get(s.id, 0) or 0),
                    "last_event_at": (
                        last_event[s.id].isoformat() if last_event.get(s.id) else None
                    ),
                },
            )
            for s in rows
        ]
    }


class StudioIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    phone: str | None = None
    owner_username: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9._-]+$")
    owner_name: str = Field(default="", max_length=120)
    owner_email: str | None = None
    face_retention_days: int | None = None


@router.post("/studios", status_code=201)
def create_studio(
    body: StudioIn,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.admin_db),
):
    """
    Creates a studio and its first owner in one step.

    A studio with no owner is unreachable, so the two are made together rather
    than leaving a window where somebody has to remember the second half. The
    owner's password is returned once and travels over WhatsApp.
    """
    cap = settings().max_face_retention_days
    days = body.face_retention_days or settings().default_face_retention_days
    if not 1 <= days <= cap:
        raise ApiError(
            422,
            "RETENTION_OUT_OF_RANGE",
            f"Face retention must be between 1 and {cap} days.",
        )

    if db.scalar(select(User).where(func.lower(User.username) == body.owner_username.lower())):
        raise ApiError(409, "USERNAME_TAKEN", "That username is already in use.")

    slug = slugify(body.name)
    if db.scalar(select(Studio).where(Studio.slug == slug)):
        slug = f"{slug}-{datetime.now(timezone.utc).strftime('%H%M%S')}"

    studio = Studio(
        name=body.name,
        slug=slug,
        city=(body.city or "").strip() or None,
        phone=body.phone,
        default_face_retention_days=days,
        status="active",
    )
    db.add(studio)
    db.flush()

    initial = generate_password()
    owner = User(
        studio_id=studio.id,
        username=body.owner_username.lower(),
        email=(body.owner_email or "").strip().lower() or None,
        name=body.owner_name or body.name,
        password_hash=hash_password(initial),
        role="owner",
        status="active",
        must_change_password=True,
    )
    db.add(owner)

    record(
        db,
        actor_user_id=who.user_id,
        actor_label=who.username,
        action="studio.created",
        target_type="studio",
        target_id=studio.id,
        studio_id=studio.id,
        detail={"name": studio.name, "face_retention_days": days},
    )
    db.commit()

    # Flat, not {studio, owner}. The panel shows one credential card and then
    # drops the new row into the same table it already renders, so it wants one
    # object of the shape it already knows plus the password.
    return {
        **studio_json(studio, {"owner_username": owner.username}),
        "initial_password": initial,
    }


class StudioPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    phone: str | None = None
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    brand_color: str | None = None
    default_face_retention_days: int | None = None


@router.patch("/studios/{studio_id}")
def update_studio(
    studio_id: str,
    body: StudioPatch,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.admin_db),
):
    """
    Changes a studio's settings, including its negotiated face retention.

    Changing retention here affects events created FROM NOW ON. Existing events
    keep the number snapshotted onto them when they were created, and guests
    keep the number snapshotted onto their session when they consented. That is
    the point of snapshotting: an agreement renegotiated in March cannot reach
    back into a wedding in January whose guests were told something else.
    """
    studio = db.get(Studio, studio_id)
    if studio is None:
        raise ApiError(404, "NOT_FOUND", "No such studio.")

    changes: dict = {}

    if body.default_face_retention_days is not None:
        cap = settings().max_face_retention_days
        if not 1 <= body.default_face_retention_days <= cap:
            # The ceiling lives in configuration and needs a restart to change.
            # A limit on how long biometric data is kept should not be editable
            # by whoever is currently logged into the admin panel.
            raise ApiError(
                422,
                "RETENTION_OUT_OF_RANGE",
                f"Face retention must be between 1 and {cap} days.",
            )
        changes["default_face_retention_days"] = [
            studio.default_face_retention_days,
            body.default_face_retention_days,
        ]
        studio.default_face_retention_days = body.default_face_retention_days

    for attr in ("name", "city", "phone", "status", "brand_color"):
        value = getattr(body, attr)
        if value is not None and getattr(studio, attr) != value:
            changes[attr] = [getattr(studio, attr), value]
            setattr(studio, attr, value)

    suspended = body.status == "suspended" and "status" in changes

    if changes:
        record(
            db,
            actor_user_id=who.user_id,
            actor_label=who.username,
            action="studio.updated",
            target_type="studio",
            target_id=studio.id,
            studio_id=studio.id,
            detail=changes,
        )

    if suspended:
        # Suspending has to take effect now, not in fifteen minutes when the
        # access tokens expire. `authenticate` already refuses a suspended
        # studio, so revoking the refresh tokens closes the loop: nobody can
        # get a new access token and nobody can sign in again.
        for member in db.scalars(select(User).where(User.studio_id == studio.id)).all():
            auth.revoke_all_for_user(db, member.id, "studio_suspended")

    db.commit()
    return studio_json(studio)


@router.post("/studios/{studio_id}/reset-password")
def reset_studio_password(
    studio_id: str,
    username: str | None = Query(default=None),
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.admin_db),
):
    """
    The manual recovery path for a studio with no email address.

    Verified out of band, against the phone number on the studio record, before
    this is called. For a studio that has no email that is not a weaker version
    of email recovery, it is the only version that exists.

    `username` is optional. The admin panel calls this from a row in the studio
    table, where the whole intent is "this studio's owner", so leaving it out
    resolves to the account the platform created with the studio. Naming a user
    explicitly is there for the case of a studio with several owners.
    """
    if username:
        user = db.scalar(
            select(User).where(
                User.studio_id == studio_id, func.lower(User.username) == username.lower()
            )
        )
    else:
        user = _owner_of(db, studio_id)

    if user is None:
        raise ApiError(404, "NOT_FOUND", "No owner account found for that studio.")

    initial = generate_password()
    user.password_hash = hash_password(initial)
    user.must_change_password = True
    user.password_changed_at = datetime.now(timezone.utc)
    auth.revoke_all_for_user(db, user.id, "admin_reset")

    record(
        db,
        actor_user_id=who.user_id,
        actor_label=who.username,
        action="member.password_reset_by_admin",
        target_type="user",
        target_id=user.id,
        studio_id=studio_id,
    )
    db.commit()
    return {
        **member_json(user, 0),
        "username": user.username,
        "initial_password": initial,
    }


# ── events and retention ───────────────────────────────────────────────


@router.get("/events")
def list_all_events(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(auth.admin_db),
):
    rows = db.execute(
        select(Event, Studio)
        .join(Studio, Studio.id == Event.studio_id)
        .order_by(Event.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": e.id,
                "name": e.name,
                "event_date": e.event_date,
                "status": e.status,
                "tier_code": e.tier_code,
                "studio_id": s.id,
                "studio_name": s.name,
                "face_retention_days": e.face_retention_days,
                "photo_retention_days": e.photo_retention_days,
                "faces_purged_at": e.faces_purged_at.isoformat() if e.faces_purged_at else None,
                "photos_purged_at": e.photos_purged_at.isoformat() if e.photos_purged_at else None,
                "created_at": e.created_at.isoformat(),
                "ended_at": e.ended_at.isoformat() if e.ended_at else None,
            }
            for e, s in rows
        ]
    }


@router.post("/events/{event_id}/purge-faces")
def purge_faces_now(
    event_id: str,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.admin_db),
):
    """
    Deletes an event's biometric data immediately, ahead of schedule.

    Galleries keep working afterwards, because `guest_matches` is not
    biometric and is not touched. What stops working is new guests joining:
    there is nothing left to match a fresh selfie against.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise ApiError(404, "NOT_FOUND", "No such event.")

    deleted = retention.purge_event_faces_now(db, event, who.user_id, who.username)
    return {"deleted": deleted, "event_id": event_id}


@router.post("/retention/run")
def run_retention(db: Session = Depends(auth.admin_db)):
    """Runs a purge pass now, instead of waiting for the scheduled one."""
    report = retention.run_all(db)
    return {
        "guest_faces": report.guest_faces,
        "photo_faces": report.photo_faces,
        "photos": report.photos,
        "objects": report.objects,
        "refresh_tokens": report.refresh_tokens,
        "login_attempts": report.login_attempts,
        "events_touched": report.events_touched,
    }


# ── overview ───────────────────────────────────────────────────────────


@router.get("/overview")
def platform_overview(db: Session = Depends(auth.admin_db)):
    """
    The five tiles at the top of the admin panel.

    `events_live_now` is the one that matters operationally. Capacity is about
    eight concurrent events on one CPX31, so this number sitting near eight is
    the signal to add a machine, and it is visible here before anything starts
    running slowly.
    """
    since = month_start()

    def count(model, *where) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    revenue_month = db.scalar(
        select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
            Payment.status == "captured", Payment.paid_at >= since
        )
    )
    revenue_all = db.scalar(
        select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(Payment.status == "captured")
    )

    return {
        "studios_active": count(Studio, Studio.status == "active"),
        "studios_suspended": count(Studio, Studio.status == "suspended"),
        "events_this_month": count(Event, Event.created_at >= since),
        # Active means paid for and not yet ended. A draft nobody paid for and
        # an event that finished last night are both not live.
        "events_live_now": count(Event, Event.status == "active"),
        "revenue_this_month_paise": int(revenue_month or 0),
        "revenue_all_time_paise": int(revenue_all or 0),
        "photos_this_month": count(Photo, Photo.created_at >= since),
    }


@router.get("/stats")
def platform_stats(db: Session = Depends(auth.admin_db)):
    """Raw totals. Not rendered anywhere; useful when checking a live server."""
    return {
        "studios": db.scalar(select(func.count()).select_from(Studio)) or 0,
        "events_total": db.scalar(select(func.count()).select_from(Event)) or 0,
        "photos": db.scalar(select(func.count()).select_from(Photo)) or 0,
        "faces": db.scalar(select(func.count()).select_from(Face)) or 0,
        "guests": db.scalar(select(func.count()).select_from(GuestSession)) or 0,
    }


@router.get("/audit")
def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    studio_id: str | None = None,
    db: Session = Depends(auth.admin_db),
):
    return {"items": [audit.as_json(e) for e in audit.recent(db, limit, studio_id)]}
