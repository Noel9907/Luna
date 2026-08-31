"""
Database connections, and the tenant boundary.

There are two engines against the same database, connecting as two different
Postgres roles.

    system   the owner. Bypasses row-level security because it owns the
             tables. Used only where there is no tenant yet.
    tenant   a role with no ownership and no BYPASSRLS. Used for every
             ordinary request, with `app.studio_id` set for the transaction.

The whole security argument rests on that second one being genuinely unable to
see other studios, rather than merely not asking for them. Application-layer
`WHERE studio_id = ...` filtering is one forgotten clause away from showing a
stranger's wedding; a policy is applied by the database to every query whether
the developer remembered or not.

The complete list of operations that legitimately have no tenant is short, and
it is the entire reason the system engine exists:

    logging in                  the user is unknown until credentials match
    refreshing a token          same
    resolving a QR token        the guest has no account at all
    loading a guest session     the token is the only identity there is
    claiming a queue job        the worker serves every studio
    the retention purge         a sweep across all tenants by definition
    the platform admin panel    two people who are meant to see everything

Anything not on that list uses `get_db`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_s = settings()

# The owner connection. Migrations, bootstrap lookups, the worker's claim, the
# purge, and platform admin requests.
system_engine = create_engine(
    _s.database_url,
    pool_pre_ping=True,  # a dropped connection should not surface as a 500
    future=True,
)

# The restricted connection. Falls back to the owner in development so a fresh
# clone runs before the role exists; `verify_production` refuses that in prod.
tenant_engine = create_engine(
    _s.database_app_url or _s.database_url,
    pool_pre_ping=True,
    future=True,
)

RLS_ACTIVE = bool(_s.database_app_url)

SystemSession = sessionmaker(bind=system_engine, autoflush=False, expire_on_commit=False)
TenantSession = sessionmaker(bind=tenant_engine, autoflush=False, expire_on_commit=False)


_SET_TENANT = text("SELECT set_config('app.studio_id', :sid, true)")


@event.listens_for(TenantSession, "after_begin")
def _pin_tenant_on_every_transaction(session: Session, _transaction, connection) -> None:
    """
    Reapplies the tenant every time a transaction starts.

    This hook is the difference between a design that works and one that leaks.
    `set_config(..., true)` is SET LOCAL, and SET LOCAL is scoped to the
    transaction, which is exactly what we want: a pooled connection handed to
    the next request must not still be carrying the previous tenant.

    But it also means that the moment a route commits, the pin is gone, and any
    query after that commit would run with no tenant set and match nothing.
    That failure is silent and looks like missing data rather than like a bug.
    Rather than asking every route to remember, the pin is stored on the
    session and reapplied here on each new transaction.

    It has to run against the `connection` handed in, not against the session.
    Calling `session.execute` here asks the session for a connection while it is
    still in the middle of producing one, and SQLAlchemy rightly refuses.
    """
    studio_id = session.info.get("studio_id")
    if studio_id:
        connection.execute(_SET_TENANT, {"sid": studio_id})


def set_tenant(db: Session, studio_id: str) -> None:
    """
    Pins this session to one studio, now and after every future commit.

    If a transaction is already open the setting is applied to it immediately;
    otherwise the hook above applies it the moment one begins. Either way no
    statement ever runs on this session without a tenant.

    The value is passed as a bind parameter rather than interpolated. SET LOCAL
    does not accept a placeholder, which is exactly why this goes through
    `set_config`, which does.
    """
    db.info["studio_id"] = str(studio_id)
    if db.in_transaction():
        db.execute(_SET_TENANT, {"sid": str(studio_id)})


def get_db() -> Iterator[Session]:
    """
    FastAPI dependency for tenant-scoped requests.

    Yields an unpinned session. The auth dependency calls `set_tenant` once the
    caller is identified, which is the only place a tenant is ever chosen.
    """
    db = TenantSession()
    try:
        yield db
    finally:
        db.close()


def get_system_db() -> Iterator[Session]:
    """FastAPI dependency for the short list of untenanted operations above."""
    db = SystemSession()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def system_session() -> Iterator[Session]:
    """The same thing outside a request, for the worker and the purge jobs."""
    db = SystemSession()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def tenant_session(studio_id: str) -> Iterator[Session]:
    """Tenant-scoped work outside a request. The worker uses this per job."""
    db = TenantSession()
    try:
        set_tenant(db, studio_id)
        yield db
    finally:
        db.close()


# Kept so existing scripts that imported `engine` keep working.
engine = system_engine
SessionLocal = SystemSession
