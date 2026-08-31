"""row level security

Creates the restricted application role and puts a policy on every table that
belongs to a studio.

Why a second Postgres role at all: RLS does not apply to a table's owner. If
the application connected as the owner, every policy written here would be
decoration. The owner runs migrations; the application connects as a role that
owns nothing, and is therefore subject to the policies.

Why policies rather than `WHERE studio_id = ...` in the application: the
application filter has to be remembered on every query anybody ever writes. A
policy is applied by the database whether or not it was remembered, so the
failure mode of forgetting becomes "no rows" instead of "another studio's
wedding".

Revision ID: 0002_rls
Revises: 45bb59d3ebf6
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_rls"
down_revision: str | None = "45bb59d3ebf6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "frame_app"

# Tables a studio owns, and the column that says which studio. `studios` keys on
# its own primary key; everything else carries a denormalised `studio_id`.
TENANT_TABLES: dict[str, str] = {
    "studios": "id",
    "users": "studio_id",
    "events": "studio_id",
    "photos": "studio_id",
    "faces": "studio_id",
    "guest_sessions": "studio_id",
    "guest_faces": "studio_id",
    "guest_matches": "studio_id",
    "processing_jobs": "studio_id",
    "payments": "studio_id",
}

# What the application role may do to each. Deliberately not "ALL": the
# application has no business deleting an event or inserting a studio, and a
# privilege never granted cannot be misused by a bug.
GRANTS: dict[str, str] = {
    "studios": "SELECT, UPDATE",
    "users": "SELECT, INSERT, UPDATE",
    "events": "SELECT, INSERT, UPDATE",
    "photos": "SELECT, INSERT, UPDATE, DELETE",
    "faces": "SELECT, INSERT, UPDATE, DELETE",
    "guest_sessions": "SELECT, INSERT, UPDATE, DELETE",
    "guest_faces": "SELECT, INSERT, DELETE",
    "guest_matches": "SELECT, INSERT, UPDATE, DELETE",
    "processing_jobs": "SELECT, INSERT, UPDATE, DELETE",
    "payments": "SELECT, INSERT, UPDATE",
}

# Never reachable from the application role at all. These are read and written
# only on the owner connection, in the handful of places listed in app/db.py.
#   refresh_tokens, login_attempts   consulted before anyone is authenticated
#   webhook_events                   written by Razorpay's callback
#   audit_log                        must not be editable by its subject
SYSTEM_ONLY = ["refresh_tokens", "login_attempts", "webhook_events", "audit_log"]


def upgrade() -> None:
    bind = op.get_bind()

    # The password goes through a session variable rather than being written
    # into this file, so the migration can live in git.
    bind.execute(
        sa.text("SELECT set_config('frame.app_password', :pw, false)"),
        {"pw": _password_from_env()},
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE format(
                    'CREATE ROLE {APP_ROLE} LOGIN PASSWORD %L',
                    coalesce(current_setting('frame.app_password', true), '{APP_ROLE}')
                );
            END IF;
        END $$;
        """
    )

    op.execute(f"GRANT CONNECT ON DATABASE {_database_name(bind)} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    for table, privileges in GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE}")

    for table in SYSTEM_ONLY:
        op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE}")

    for table, column in TENANT_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        # `current_setting(..., true)` returns NULL when nothing has been set,
        # and a comparison against NULL is NULL rather than true, so a
        # connection that has not chosen a tenant sees no rows at all. The
        # default is closed, which is the only safe default for this.
        #
        # WITH CHECK repeats the predicate for writes. Without it the policy
        # would stop a studio reading another studio's rows while still letting
        # it create rows stamped with someone else's studio_id.
        predicate = f"{column}::text = current_setting('app.studio_id', true)"
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                FOR ALL TO {APP_ROLE}
                USING ({predicate})
                WITH CHECK ({predicate})
            """
        )

    # Note what is deliberately NOT done here: FORCE ROW LEVEL SECURITY.
    #
    # A table's owner is exempt from its policies. Since migrations, login, the
    # worker's claim and the purge all run as the owner, forcing would mean
    # writing a second policy to let the owner back in, which cancels out the
    # forcing and buys nothing. The isolation guarantee does not come from
    # FORCE; it comes from the application connecting as a role that owns
    # nothing. That is the one thing that must not be got wrong in deployment,
    # which is why `Settings.verify_production` refuses to boot without it.


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE}")
    # The role itself is left in place. Dropping it fails if anything else in
    # the cluster still refers to it, and an unused login role is harmless.


def _password_from_env() -> str:
    import os

    return os.environ.get("APP_DB_PASSWORD", "frame_app")


def _database_name(bind) -> str:
    return bind.execute(sa.text("SELECT current_database()")).scalar_one()
