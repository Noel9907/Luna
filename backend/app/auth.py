"""
Authentication: who is calling, and the dependencies that pin them to a tenant.

The token pair here is the standard one, and the reasons for each half matter:

An ACCESS token is a signed JWT that is never stored. Nothing has to be looked
up to trust it, which is why it can be checked on every request for free. The
price is that it cannot be withdrawn, so it lives fifteen minutes.

A REFRESH token is a random string that is stored, as a digest, and may be
redeemed exactly once. Because it is stored it can be revoked instantly, and
because it is single use, a stolen one collides with the real user's next
refresh and gives the theft away. See `rotate_refresh_token`.

Accounts are created by hand and there is no self-signup. Credentials are read
out over WhatsApp, which is why every new account starts with
`must_change_password` set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, get_system_db, set_tenant
from app.errors import ApiError
from app.models import LoginAttempt, RefreshToken, User
from app.security import (
    Principal,
    fingerprint,
    hash_password,
    make_access_token,
    needs_rehash,
    new_opaque_token,
    read_access_token,
    refresh_expiry,
    verify_password,
)

# A password that fails this is rejected at the point it is set, never at login.
MIN_PASSWORD_LENGTH = 10


# ── request identity ───────────────────────────────────────────────────


def principal(authorization: str | None = Header(default=None)) -> Principal:
    """
    The caller, taken from the bearer token. No database access at all.

    Every authenticated route depends on this, directly or through one of the
    wrappers below, so it is the single place a request becomes an identity.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
    who = read_access_token(authorization.split(" ", 1)[1].strip())
    if who is None:
        raise ApiError(401, "UNAUTHENTICATED", "Your session has expired. Sign in again.")
    return who


def studio_db(
    who: Principal = Depends(principal),
    db: Session = Depends(get_db),
) -> Session:
    """
    A database session pinned to the caller's studio.

    This is the dependency almost every route wants. From here on the
    connection physically cannot return another studio's rows, so the route
    below is free to write the query it means rather than the query plus a
    tenant filter it must not forget.
    """
    if who.is_platform_admin or who.studio_id is None:
        # Platform admins have no tenant to pin. They belong on `admin_db`,
        # which uses the owner connection; sending them here would silently
        # produce empty results rather than an obvious error.
        raise ApiError(403, "FORBIDDEN", "This endpoint is for studio accounts.")
    set_tenant(db, who.studio_id)
    return db


def active_studio_db(
    who: Principal = Depends(principal),
    db: Session = Depends(studio_db),
) -> Session:
    """
    Same, but refuses a caller who still has to change their password.

    Without this, a photographer given a WhatsApp password could simply never
    visit the change screen and keep using the temporary one forever.
    """
    if who.must_change_password:
        raise ApiError(
            403, "PASSWORD_CHANGE_REQUIRED", "Set a new password before continuing."
        )
    return db


def owner_db(
    who: Principal = Depends(principal),
    db: Session = Depends(active_studio_db),
) -> Session:
    """Managing photographers and paying for events is the owner's alone."""
    if who.role != "owner":
        raise ApiError(403, "FORBIDDEN", "Only the studio owner can do that.")
    return db


def admin_db(
    who: Principal = Depends(principal),
    db: Session = Depends(get_system_db),
) -> Session:
    """
    The platform admin panel. Two people, and the owner connection.

    An admin is not a tenant, so there is nothing to pin. They see across
    studios by design, which is precisely why this is a different dependency
    and a different connection rather than a role check bolted onto the
    tenant path.
    """
    if not who.is_platform_admin:
        raise ApiError(403, "FORBIDDEN", "Not permitted.")
    return db


def client_ip(request: Request) -> str | None:
    """
    Behind Caddy the socket address is the proxy, so the forwarded header is
    the real client. Only the first entry is trusted, and only because the
    proxy in front of this is ours.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


# ── login ──────────────────────────────────────────────────────────────


def _too_many_attempts(db: Session, identifier: str) -> bool:
    s = settings()
    since = datetime.now(timezone.utc) - timedelta(minutes=s.login_window_minutes)
    recent = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.identifier == identifier, LoginAttempt.created_at > since)
    )
    return (recent or 0) >= s.login_max_attempts


def authenticate(
    db: Session, identifier: str, password: str, user_agent: str | None, ip: str | None
) -> tuple[User, str, str, int]:
    """
    Checks credentials and issues a fresh token pair.

    Runs on the owner connection: there is no tenant to scope to until we know
    who this is, which is the first entry on the short list in `app/db.py`.

    `identifier` is a username OR an email. Many small studios in India have no
    email address at all, so username is the real identifier and email is the
    convenience.
    """
    identifier = identifier.strip()

    if _too_many_attempts(db, identifier):
        raise ApiError(
            429,
            "TOO_MANY_ATTEMPTS",
            "Too many sign-in attempts. Wait a few minutes and try again.",
        )

    user = db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == identifier.lower(),
                func.lower(User.email) == identifier.lower(),
            )
        )
    )

    # One message and one code for every failure: wrong username, wrong
    # password, disabled account. Distinguishing them tells an attacker which
    # usernames exist, and tells them for free.
    def reject() -> None:
        db.add(LoginAttempt(identifier=identifier, ip=ip))
        db.commit()
        raise ApiError(401, "INVALID_CREDENTIALS", "That username or password is not right.")

    if user is None:
        # Hash anyway. Returning immediately makes a missing user measurably
        # faster than a wrong password, and that timing difference is itself a
        # way to enumerate accounts.
        hash_password(password)
        reject()

    if not verify_password(user.password_hash, password):
        reject()

    if user.status != "active" or (user.studio_id and _studio_suspended(db, user.studio_id)):
        reject()

    # The plaintext is in hand exactly here, so it is the only moment an old
    # hash can be upgraded to the current cost parameters.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.last_login_at = datetime.now(timezone.utc)
    db.query(LoginAttempt).filter(LoginAttempt.identifier == identifier).delete()

    access, refresh, ttl = issue_session(db, user, user_agent, ip)
    db.commit()
    return user, access, refresh, ttl


def _studio_suspended(db: Session, studio_id: str) -> bool:
    from app.models import Studio

    studio = db.get(Studio, studio_id)
    return studio is None or studio.status != "active"


def issue_session(
    db: Session, user: User, user_agent: str | None, ip: str | None
) -> tuple[str, str, int]:
    """
    Starts a brand new token family for this user.

    Used by login and by a password change. A new family, rather than
    continuing an old one, is the point: whatever chain existed before is now
    unrelated to this one, so revoking it later cannot reach back and log out
    the session that just started.
    """
    access, ttl = make_access_token(
        user.id, user.studio_id, user.role, user.username, user.must_change_password
    )
    refresh = _issue_refresh(db, user, str(uuid.uuid4()), user_agent, ip)
    return access, refresh, ttl


def _issue_refresh(
    db: Session, user: User, family_id: str, user_agent: str | None, ip: str | None
) -> str:
    raw = new_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=fingerprint(raw),
            expires_at=refresh_expiry(),
            user_agent=(user_agent or "")[:300] or None,
            ip=ip,
        )
    )
    return raw


def rotate_refresh_token(
    db: Session, raw_token: str, user_agent: str | None, ip: str | None
) -> tuple[User, str, str, int]:
    """
    Exchanges a refresh token for a new pair, and detects theft while doing it.

    Every token may be redeemed once. On redemption it is marked used and a
    successor is issued carrying the same `family_id`.

    So consider a token that is stolen. Either the thief uses it first and the
    real user's app refreshes next, or the other way round. Whichever happens
    second presents a token already marked used, and there is no innocent
    explanation for that. At that point we cannot tell which party is the
    thief, so the whole family is revoked and both are logged out. The real
    user signs in again and is mildly annoyed; the thief is left holding
    nothing and cannot get back in without the password.

    Without rotation a stolen refresh token is a thirty day session that
    nobody ever notices.
    """
    digest = fingerprint(raw_token)

    # FOR UPDATE, because a flaky connection genuinely does produce two
    # refreshes of the same token milliseconds apart. Without the lock both
    # would see `used_at` as null, both would succeed, and the second would
    # then look like theft to the next request.
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest).with_for_update())

    if row is None:
        raise ApiError(401, "INVALID_REFRESH", "Please sign in again.")

    if row.used_at is not None:
        _revoke_family(db, row.family_id, "reuse_detected")
        db.commit()
        raise ApiError(401, "INVALID_REFRESH", "Please sign in again.")

    if row.revoked_at is not None or row.expires_at < datetime.now(timezone.utc):
        raise ApiError(401, "INVALID_REFRESH", "Please sign in again.")

    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        raise ApiError(401, "INVALID_REFRESH", "Please sign in again.")

    row.used_at = datetime.now(timezone.utc)
    new_raw = _issue_refresh(db, user, row.family_id, user_agent, ip)
    access, ttl = make_access_token(
        user.id, user.studio_id, user.role, user.username, user.must_change_password
    )
    db.commit()
    return user, access, new_raw, ttl


def _revoke_family(db: Session, family_id: str, reason: str) -> int:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for r in rows:
        r.revoked_at = now
        r.revoked_reason = reason
    return len(rows)


def revoke_all_for_user(db: Session, user_id: str, reason: str) -> int:
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for r in rows:
        r.revoked_at = now
        r.revoked_reason = reason
    return len(rows)


def logout(db: Session, raw_token: str) -> None:
    """Revokes the whole family, so every device in that chain is signed out."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == fingerprint(raw_token)))
    if row is not None:
        _revoke_family(db, row.family_id, "logout")
        db.commit()


def set_password(db: Session, user: User, new_password: str) -> None:
    """
    Changes a password and invalidates every existing session.

    People change a password precisely when they think someone else has it, so
    leaving the other sessions alive defeats the point of changing it.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ApiError(
            422,
            "PASSWORD_TOO_WEAK",
            f"Use at least {MIN_PASSWORD_LENGTH} characters.",
        )
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    revoke_all_for_user(db, user.id, "password_changed")


def purge_expired_refresh_tokens(db: Session) -> int:
    """Housekeeping, called by the retention job. Nothing depends on old rows."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    result = db.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
    return result.rowcount or 0
