"""
Sign in, refresh, change password, and who am I.

Everything except `/me` runs on the owner connection. That is not a shortcut:
until credentials have matched there is no tenant to scope a query to, so
these are the first entries on the short list in `app/db.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import auth
from app.db import get_db, get_system_db, set_tenant
from app.errors import ApiError
from app.models import Studio, User
from app.security import Principal, verify_password
from app.storage import get_storage

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1)


class RefreshIn(BaseModel):
    refresh_token: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1)


def _pair(access: str, refresh: str, ttl: int) -> dict:
    return {"access_token": access, "refresh_token": refresh, "expires_in": ttl}


@router.post("/auth/login")
def login(
    body: LoginIn,
    request: Request,
    db: Session = Depends(get_system_db),
):
    _user, access, refresh, ttl = auth.authenticate(
        db,
        body.identifier,
        body.password,
        user_agent=request.headers.get("user-agent"),
        ip=auth.client_ip(request),
    )
    return _pair(access, refresh, ttl)


@router.post("/auth/refresh")
def refresh(
    body: RefreshIn,
    request: Request,
    db: Session = Depends(get_system_db),
):
    _user, access, new_refresh, ttl = auth.rotate_refresh_token(
        db,
        body.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip=auth.client_ip(request),
    )
    return _pair(access, new_refresh, ttl)


@router.post("/auth/logout", status_code=204)
def logout(body: RefreshIn, db: Session = Depends(get_system_db)):
    # Never reports whether the token existed. A logout endpoint that
    # distinguishes them is a way to test whether a stolen token is still live.
    auth.logout(db, body.refresh_token)
    return None


@router.post("/auth/password")
def change_password(
    body: PasswordIn,
    request: Request,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(get_system_db),
):
    """
    Changes the password and returns a fresh pair.

    Deliberately NOT behind `active_studio_db`: a user who must change their
    password has to be able to reach exactly this one endpoint, and nothing
    else. It also runs on the owner connection so a platform admin, who has no
    tenant, can use the same route as everybody else.
    """
    user = db.get(User, who.user_id)
    if user is None or user.status != "active":
        raise ApiError(401, "UNAUTHENTICATED", "Sign in again.")

    if not verify_password(user.password_hash, body.current_password):
        raise ApiError(401, "INVALID_CREDENTIALS", "That current password is not right.")

    auth.set_password(db, user, body.new_password)

    # A new pair, because `set_password` just revoked every token this user
    # had, including the one that made this request.
    access, new_refresh, ttl = auth.issue_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=auth.client_ip(request),
    )
    db.commit()
    return _pair(access, new_refresh, ttl)


@router.get("/me")
def me(
    who: Principal = Depends(auth.principal),
    db: Session = Depends(get_db),
    sdb: Session = Depends(get_system_db),
):
    """
    The caller and their studio.

    A platform admin has no studio to pin, so they are read on the owner
    connection; everybody else is read through the tenant connection like any
    other request.
    """
    if who.is_platform_admin or who.studio_id is None:
        user = sdb.get(User, who.user_id)
        if user is None:
            raise ApiError(401, "UNAUTHENTICATED", "Sign in again.")
        return {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "studio": None,
        }

    set_tenant(db, who.studio_id)
    user = db.get(User, who.user_id)
    studio = db.get(Studio, who.studio_id)
    if user is None or studio is None:
        raise ApiError(401, "UNAUTHENTICATED", "Sign in again.")

    logo_url = None
    if studio.brand_logo_key:
        logo_url = get_storage().signed_get_url(studio.brand_logo_key, seconds=86400)

    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "must_change_password": user.must_change_password,
        "studio": {
            "id": studio.id,
            "name": studio.name,
            "phone": studio.phone,
            "brand_color": studio.brand_color,
            "brand_logo_url": logo_url,
        },
    }
