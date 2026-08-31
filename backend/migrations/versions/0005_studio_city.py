"""studios have a city

The admin panel lists studios by name, owner, city and phone, because when you
are selling across a country, the city is how you tell two similarly named
studios apart on a call. The column was missing, so the panel had nowhere to read it from.

Revision ID: 0005_city
Revises: 0004_audit
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_city"
down_revision: str | None = "0004_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("studios", sa.Column("city", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("studios", "city")
