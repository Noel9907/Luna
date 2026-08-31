"""
Tables.

Two rules run through all of this.

`studio_id` is on every tenant-owned table, including the guest tables, even
where it could be derived through a join. Row-level security policies are
evaluated per row on every query, so a policy that has to join to find its
tenant is both slow and easy to get subtly wrong. A denormalised column makes
every policy a single equality test.

Anything a studio bought is SNAPSHOTTED onto the row at purchase and never
resolved through a foreign key at read time, so changing a price or a retention
default next year cannot rewrite what somebody already paid for.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.faces import EMBEDDING_DIM


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ── tenancy and people ─────────────────────────────────────────────────


class Studio(Base):
    __tablename__ = "studios"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Shown in the admin list. Selling across India, the city is how you tell
    # two similarly named studios apart on a phone call.
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)

    brand_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    brand_logo_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The negotiated face retention for this studio. Copied onto each event at
    # creation, so raising it later cannot reach back into events whose guests
    # already consented to a shorter window. Capped by MAX_FACE_RETENTION_DAYS.
    default_face_retention_days: Mapped[int] = mapped_column(Integer, default=30)

    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    """
    A person who logs in. Created by hand, never by self-signup.

    `studio_id` is NULL for platform administrators, which is exactly two
    people. That null is also what the row-level security policies key on: an
    admin has no tenant, so their branch of the policy is separate rather than
    being a studio that happens to see everything.

    `username` is globally unique, not unique per studio, because login accepts
    a bare username with no tenant context. Many small Indian studios have no
    email address at all, so username is the primary identifier and email is
    the optional one.
    """

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str | None] = mapped_column(
        ForeignKey("studios.id", ondelete="CASCADE"), nullable=True, index=True
    )
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)

    # platform_admin | owner | photographer
    role: Mapped[str] = mapped_column(String(20), default="photographer")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled

    # Credentials go out over WhatsApp, so the first login must force a change.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (
        # Unique only where an email exists. A plain unique constraint would let
        # exactly one user in the whole system have no email.
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )


class RefreshToken(Base):
    """
    One issued refresh token. Rotated on every use.

    Only the SHA-256 of the token is stored. A database leak then yields
    nothing usable, for the same reason passwords are hashed.

    `family_id` is what makes theft detectable. Every rotation carries the
    family forward, and each token may be redeemed exactly once. If a token
    that has already been redeemed shows up again, two parties hold the same
    token, so the whole family is revoked and both are logged out. The real
    user simply logs in again; the thief gets nothing.
    """

    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LoginAttempt(Base):
    """
    Failed logins, for rate limiting.

    Keyed on the identifier as typed rather than on a resolved user, so probing
    for which usernames exist is throttled exactly like guessing a password for
    one that does.
    """

    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    identifier: Mapped[str] = mapped_column(String(200), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


# ── events ─────────────────────────────────────────────────────────────


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    event_date: Mapped[str] = mapped_column(String(10))

    # draft -> active -> ended.  Only the payment webhook promotes draft to
    # active. A browser returning from checkout never does, because browsers
    # get closed and phones lose signal on the confirmation screen.
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    qr_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # Snapshot of what was bought.
    tier_code: Mapped[str] = mapped_column(String(16), default="pro")
    price_paise: Mapped[int] = mapped_column(Integer, default=0)
    branding_mode: Mapped[str] = mapped_column(String(16), default="studio")
    photo_retention_days: Mapped[int] = mapped_column(Integer, default=90)

    # Snapshotted like the rest, and shown to the guest on the consent screen as
    # a real date. A promise a guest can read is worth more than one in a table.
    face_retention_days: Mapped[int] = mapped_column(Integer, default=30)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # When a worker last took a job for this event. This one column is what
    # makes the queue fair: the claim query serves whichever event has waited
    # longest, so a large backlog cannot hold the machine against a live event.
    # NULL means never served, which correctly puts a brand new event first.
    last_job_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Set once the retention jobs have run, so a purged event is auditable
    # rather than merely empty.
    faces_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    photos_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_events_studio_created", "studio_id", "created_at"),)


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    storage_key: Mapped[str] = mapped_column(Text)
    # Written by the worker. The gallery serves this, never the original: a
    # phone on venue wifi should pull 40KB, not 4MB.
    thumb_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # pending -> uploaded -> processing -> done, or failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    face_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_photos_event_created", "event_id", "created_at"),)


class Face(Base):
    """One face found in one photograph. Biometric, so subject to the purge."""

    __tablename__ = "faces"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)

    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    bbox_x: Mapped[int] = mapped_column(Integer)
    bbox_y: Mapped[int] = mapped_column(Integer)
    bbox_w: Mapped[int] = mapped_column(Integer)
    bbox_h: Mapped[int] = mapped_column(Integer)
    det_score: Mapped[float] = mapped_column(Float)
    blur_score: Mapped[float] = mapped_column(Float)

    # Embeddings from different models are not comparable. Without this column a
    # model upgrade silently starts returning wrong matches instead of an
    # obvious, filterable inconsistency.
    model_version: Mapped[str] = mapped_column(String(40), default="sface-2021dec")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ── guests ─────────────────────────────────────────────────────────────


class GuestSession(Base):
    __tablename__ = "guest_sessions"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)

    # Hashed like a refresh token, and for the same reason: this one string is
    # the only thing standing between a stranger and a guest's gallery.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # What the guest was actually promised, frozen at consent time.
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    consent_face_retention_days: Mapped[int] = mapped_column(Integer, default=30)

    # A selfie costs real CPU, so attempts are counted and capped.
    selfie_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GuestFace(Base):
    """
    A guest selfie embedding. This is the biometric data.

    Purged on the schedule the guest was shown at consent. `guest_matches`
    survives that purge because it holds no biometric data, so galleries keep
    working after the embeddings are gone.
    """

    __tablename__ = "guest_faces"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    guest_session_id: Mapped[str] = mapped_column(
        ForeignKey("guest_sessions.id", ondelete="CASCADE"), index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    model_version: Mapped[str] = mapped_column(String(40), default="sface-2021dec")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GuestMatch(Base):
    """Not biometric: a guest id, a photo id, and a score."""

    __tablename__ = "guest_matches"
    guest_session_id: Mapped[str] = mapped_column(
        ForeignKey("guest_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    photo_id: Mapped[str] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    similarity: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (Index("ix_guest_matches_session_created", "guest_session_id", "created_at"),)


# ── the queue ──────────────────────────────────────────────────────────


class Job(Base):
    """
    The processing queue. Postgres, not a separate service.

    Claimed with FOR UPDATE SKIP LOCKED, which lets many workers take different
    rows concurrently without blocking each other or handing the same job out
    twice.

    `event_id` is duplicated here rather than joined through `photos` because
    the claim query runs on every poll of every worker and ranks jobs within
    their own event. A join in that statement would be paid thousands of times
    an hour to learn something the row could simply carry.
    """

    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (
        UniqueConstraint("photo_id", name="uq_jobs_photo"),
        # The claim query's working set is only the rows still waiting. A
        # partial index keeps it the size of the backlog rather than of all
        # history, which matters because it is scanned on every poll.
        Index(
            "ix_jobs_claimable",
            "event_id",
            "created_at",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )


# ── billing ────────────────────────────────────────────────────────────


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    studio_id: Mapped[str] = mapped_column(ForeignKey("studios.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)

    tier_code: Mapped[str] = mapped_column(String(16))
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="INR")

    # created -> paid, or failed. Only ever moved by the webhook.
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    provider: Mapped[str] = mapped_column(String(20), default="razorpay")
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)  # upi, card, netbanking

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class WebhookEvent(Base):
    """
    Every webhook Razorpay delivers, recorded before it is acted on.

    Payment providers retry, and they do not promise to deliver exactly once.
    (provider, external_id) is unique, so a redelivery collides on insert and is
    dropped instead of activating an event twice or double-crediting a studio.
    """

    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(20), default="razorpay")
    external_id: Mapped[str] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_webhook_external"),)


# ── audit ──────────────────────────────────────────────────────────────


class AuditLog(Base):
    """
    Who did what, for the things that get disputed later.

    Deliberately not a general activity feed. It records administrative acts:
    creating accounts, resetting passwords, changing retention, purging faces,
    activating events. If a studio ever asks why their data is gone, this is
    the answer.
    """

    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=uid)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(120))  # survives the user being deleted
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    studio_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
