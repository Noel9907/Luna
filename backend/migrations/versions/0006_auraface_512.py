"""Move face embeddings from SFace 128-dim to AuraFace 512-dim.

Revision ID: 0006_auraface
Revises: 0005_city
Create Date: 2026-09-06

THIS MIGRATION DELETES EVERY EMBEDDING AND EVERY MATCH. Read before running.

WHY IT HAS TO

Embeddings from two models are not comparable. There is no conversion from a
128-dim SFace vector to a 512-dim AuraFace one; they are different coordinate
systems built by different networks. A migration that tried to keep the old rows
would leave a table where cosine distance means one thing for some rows and
something else for others, and the failure would surface as wrong galleries at a
wedding rather than as an error. So the old vectors go, and every photograph is
queued to be indexed again.

WHY THE MOVE IS WORTH THAT

Measured on the 348-photograph event in storage, using pairs of faces from the
same photograph, which are different people by construction:

                      SFace          AuraFace
    impostor mean     0.200          0.151
    impostor sd       0.112          0.088
    impostor max      0.532          0.424
    FMR at 0.363      7.8e-2         4.5e-3
    clean from        0.55           0.45
    cost              33 ms/face     85 ms/face

SFace needed a 0.55 threshold to keep strangers out, and the average genuine
match scored 0.549. The threshold required for safety sat above the middle of
the true matches, so no usable operating point existed. That is the entire
reason for this migration.

    scripts/measure_impostors.py <folder> --compare   reproduces the table

WHAT IT COSTS AT RUNTIME

Roughly 2.6x per face, but embedding is only part of indexing a photograph. At
2.44 faces per photograph that moves a photo from ~350ms to ~480ms, so about 8
concurrent events becomes about 6 on the same box.

WHAT BREAKS FOR PEOPLE, NOT FOR DATA

Guests who already took a selfie lose it and must scan and register again. Their
galleries are empty until they do. Never run this during a live event. The
pre-wedding checklist in deploy/DEPLOY.md is the right place to confirm it ran.

BEFORE RUNNING

    python scripts/download_models.py --auraface     ~250 MB
    pip install onnxruntime
    set FACE_BACKEND=auraface in .env

The dimension is written here as a literal rather than read from settings.
A migration is a fixed step in history: if it read config it would do something
different depending on when it ran, and replaying it on a fresh database would
not reproduce this one.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006_auraface"
down_revision: str | None = "0005_city"
branch_labels = None
depends_on = None

OLD_DIM = 128
NEW_DIM = 512


def _swap_dim(dim: int) -> None:
    # Order matters: guest_matches references guest_sessions and photos, and
    # holds scores computed by the old model, so it is meaningless either way.
    # Clearing it first also means the vector columns are altered on empty
    # tables, where there is nothing for Postgres to try to cast.
    op.execute("DELETE FROM guest_matches")
    op.execute("DELETE FROM guest_faces")
    op.execute("DELETE FROM faces")

    # Dropped and re-added rather than ALTER COLUMN ... TYPE. Changing a vector
    # column's width relies on pgvector's typmod cast, which is documented as
    # "technically possible" rather than guaranteed, and this is a migration that
    # deletes data: it must not be the thing that half-succeeds. The rows are
    # already gone by this point, so there is nothing left to preserve, and the
    # only cost is that `embedding` moves to the end of the column order, which
    # nothing reads positionally. No index or constraint sits on it.
    for table in ("faces", "guest_faces"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN embedding")
        op.execute(f"ALTER TABLE {table} ADD COLUMN embedding vector({dim}) NOT NULL")

    # Re-index everything. `processing_jobs` has a unique constraint on photo_id,
    # so existing rows are reset rather than duplicated, and only photographs
    # that somehow lost their job row get a new one.
    op.execute(
        """
        UPDATE photos
           SET status = 'pending'
         WHERE status IN ('done', 'processing', 'failed')
        """
    )
    op.execute(
        """
        UPDATE processing_jobs
           SET status = 'pending', attempts = 0, locked_at = NULL, last_error = NULL
        """
    )
    op.execute(
        """
        INSERT INTO processing_jobs
                    (id, studio_id, event_id, photo_id, status, attempts, created_at)
             SELECT gen_random_uuid(), p.studio_id, p.event_id, p.id, 'pending', 0, now()
               FROM photos p
              WHERE NOT EXISTS (
                    SELECT 1 FROM processing_jobs j WHERE j.photo_id = p.id
              )
        """
    )

    # Every event is about to be re-indexed from nothing, so no event has a
    # stronger claim on the queue than any other. NULL sorts first under the
    # fair-share ordering, which starts the round robin clean instead of letting
    # whichever event happened to be served last go to the back for a whole run.
    op.execute("UPDATE events SET last_job_claimed_at = NULL")


def upgrade() -> None:
    _swap_dim(NEW_DIM)


def downgrade() -> None:
    """
    Back to SFace. Destroys just as much as the upgrade did, for the same reason:
    512-dim vectors do not become 128-dim ones. Set FACE_BACKEND=sface as well,
    or the application will declare 512 against a 128 column.
    """
    _swap_dim(OLD_DIM)
