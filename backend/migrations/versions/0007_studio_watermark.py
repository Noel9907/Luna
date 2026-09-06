"""Per-studio watermark settings.

Revision ID: 0007_watermark
Revises: 0006_auraface
Create Date: 2026-09-06

The watermark started as one environment variable, which is fine for one studio
and wrong for a product sold to many: the mark belongs to the studio, not to the
server. These columns move it onto the tenant, so two studios on the same box
brand their own photographs and neither can see or change the other's.

`brand_logo_key` already existed and is reused, so the logo a studio uploads for
its guest gallery is the same file that marks its photographs. One upload, not
two, and no way for the two to drift apart.

Additive and nullable-safe: existing studios come out with the watermark off,
which is what they had before this ran.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_watermark"
down_revision: str | None = "0006_auraface"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "studios",
        sa.Column(
            "watermark_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # Width of the mark as a fraction of the photograph's width, and its opacity.
    # Stored per studio because a wordmark and a monogram want very different
    # sizes, and picking one number for everybody would suit nobody.
    op.add_column(
        "studios",
        sa.Column(
            "watermark_scale", sa.Float(), nullable=False, server_default=sa.text("0.20")
        ),
    )
    op.add_column(
        "studios",
        sa.Column(
            "watermark_opacity", sa.Float(), nullable=False, server_default=sa.text("0.75")
        ),
    )


def downgrade() -> None:
    op.drop_column("studios", "watermark_opacity")
    op.drop_column("studios", "watermark_scale")
    op.drop_column("studios", "watermark_enabled")
