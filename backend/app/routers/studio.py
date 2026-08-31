"""
The people in a studio.

There is no invitation flow and no email is ever sent. The owner creates an
account, the server generates a password and shows it once, and the owner sends
it on over WhatsApp. That is how these studios actually work, and building an
email-based invitation would have excluded the customers this is for: plenty of
small studios in Kerala have a phone number and no email address at all.
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth
from app.audit import record
from app.errors import ApiError
from app.models import Photo, User
from app.security import Principal, hash_password

router = APIRouter(tags=["members"])

# Ambiguous characters removed. This password gets read off a screen, typed
# into a phone, and sometimes read aloud over a call, so 0/O and 1/l/I are a
# support burden rather than entropy worth having.
ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
INITIAL_PASSWORD_LENGTH = 12


def generate_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(INITIAL_PASSWORD_LENGTH))


def member_json(u: User, photos_uploaded: int) -> dict:
    return {
        "user_id": u.id,
        "username": u.username,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "status": u.status,
        "photos_uploaded": photos_uploaded,
        "must_change_password": u.must_change_password,
        "last_active_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat(),
    }


@router.get("/studio/members")
def list_members(db: Session = Depends(auth.active_studio_db)):
    """
    Everyone in the caller's studio.

    No studio id in the path and no studio filter in the query: the connection
    is pinned, so this returns the caller's own people and cannot return
    anybody else's.
    """
    counts = dict(
        db.execute(
            select(Photo.uploaded_by, func.count())
            .where(Photo.uploaded_by.is_not(None))
            .group_by(Photo.uploaded_by)
        ).all()
    )
    rows = db.scalars(select(User).order_by(User.created_at)).all()
    return {"items": [member_json(u, counts.get(u.id, 0)) for u in rows]}


class MemberIn(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9._-]+$")
    name: str = Field(default="", max_length=120)
    email: str | None = None
    role: str = Field(pattern="^(photographer|owner)$")


@router.post("/studio/members", status_code=201)
def create_member(
    body: MemberIn,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.owner_db),
):
    """
    Creates a photographer and returns their initial password once.

    That password is shown in this response and is not stored anywhere in
    readable form, so it genuinely cannot be retrieved again. If the owner
    loses it before passing it on, the fix is to reset it, not to look it up.
    """
    # Usernames are globally unique because login takes a bare username with no
    # studio context. This check runs on the tenant connection, which cannot
    # see other studios, so a clash outside this studio is caught by the
    # database constraint instead and reported the same way.
    taken = db.scalar(select(User).where(func.lower(User.username) == body.username.lower()))
    if taken is not None:
        raise ApiError(409, "USERNAME_TAKEN", "That username is already in use.")

    initial = generate_password()
    user = User(
        studio_id=who.studio_id,
        username=body.username.lower(),
        email=(body.email or "").strip().lower() or None,
        name=body.name or body.username,
        password_hash=hash_password(initial),
        role=body.role,
        status="active",
        must_change_password=True,
    )
    db.add(user)

    record(
        db,
        actor_user_id=who.user_id,
        actor_label=who.username,
        action="member.created",
        target_type="user",
        target_id=user.id,
        studio_id=who.studio_id,
        detail={"username": user.username, "role": user.role},
    )

    try:
        db.commit()
    except IntegrityError as exc:
        # Only a constraint violation means the name is taken. Catching every
        # exception here would report "username taken" for a permission error
        # or a dead connection, and send whoever debugs it in the wrong
        # direction entirely.
        db.rollback()
        raise ApiError(409, "USERNAME_TAKEN", "That username is already in use.") from exc

    return {**member_json(user, 0), "initial_password": initial}


@router.post("/studio/members/{user_id}/reset-password")
def reset_member_password(
    user_id: str,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.owner_db),
):
    """
    Issues a new initial password and signs that person out everywhere.

    Revoking their tokens is the point. Resetting a password because somebody
    else has it, while leaving that person's existing sessions alive, changes
    nothing.
    """
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "No such member.")

    initial = generate_password()
    user.password_hash = hash_password(initial)
    user.must_change_password = True
    user.password_changed_at = datetime.now(timezone.utc)

    record(
        db,
        actor_user_id=who.user_id,
        actor_label=who.username,
        action="member.password_reset",
        target_type="user",
        target_id=user.id,
        studio_id=who.studio_id,
    )
    db.commit()

    # Token revocation needs the owner connection: refresh_tokens is one of the
    # tables the application role cannot reach at all.
    from app.db import system_session

    with system_session() as sdb:
        auth.revoke_all_for_user(sdb, user.id, "password_reset")
        sdb.commit()

    return {**member_json(user, 0), "initial_password": initial}


@router.delete("/studio/members/{user_id}", status_code=204)
def remove_member(
    user_id: str,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.owner_db),
):
    """
    Removes someone's access.

    Their photographs stay with the event. They belong to the studio, not to
    the person who happened to press upload, and deleting a wedding's coverage
    because a second shooter left would be indefensible. `photos.uploaded_by`
    is ON DELETE SET NULL for exactly this reason.
    """
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "No such member.")

    if user.id == who.user_id:
        raise ApiError(409, "CANNOT_REMOVE_SELF", "You cannot remove your own account.")

    if user.role == "owner":
        owners = db.scalar(
            select(func.count()).select_from(User).where(User.role == "owner", User.id != user.id)
        )
        if not owners:
            raise ApiError(409, "LAST_OWNER", "A studio must always have at least one owner.")

    # Disabled rather than deleted. A deleted row takes the audit trail's
    # foreign key with it, and "who uploaded these 600 photographs" is a
    # question that gets asked months later.
    user.status = "disabled"
    record(
        db,
        actor_user_id=who.user_id,
        actor_label=who.username,
        action="member.removed",
        target_type="user",
        target_id=user.id,
        studio_id=who.studio_id,
        detail={"username": user.username},
    )
    db.commit()

    from app.db import system_session

    with system_session() as sdb:
        auth.revoke_all_for_user(sdb, user.id, "removed")
        sdb.commit()

    return Response(status_code=204)
