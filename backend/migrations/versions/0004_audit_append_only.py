"""audit log is append-only for the application role

The application role was given no access to `audit_log` at all, which was too
strict in one direction and correct in the other.

An audit entry has to be written in the SAME transaction as the change it
describes, otherwise a crash between the two leaves either an unexplained
change or a record of something that never happened. Actions like creating a
photographer run on the tenant connection, so that connection has to be able to
insert here.

But only insert. No SELECT, no UPDATE, no DELETE. A studio can add to the trail
and can never read it, edit it, or remove the entry describing what they just
did. Append-only is exactly the privilege an audit log wants, and it falls out
of the grant rather than needing a trigger to enforce it.

Row-level security is deliberately not enabled on this table. There is nothing
to filter for a role that cannot read it, and adding a policy would only
suggest that reading is expected.

Revision ID: 0004_audit
Revises: c3b1ea08e3b4
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_audit"
down_revision: str | None = "c3b1ea08e3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "frame_app"


def upgrade() -> None:
    op.execute(f"GRANT INSERT ON audit_log TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE INSERT ON audit_log FROM {APP_ROLE}")
