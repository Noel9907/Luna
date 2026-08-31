"""
The two queries that are the product.

Both live here rather than in a router so the worker and the API call exactly
the same code. A guest arriving and a photograph arriving are the same operation
run from opposite directions.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings


def _vec_literal(v: np.ndarray) -> str:
    """pgvector accepts '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def search_photos_for_guest(
    db: Session, event_id: str, embedding: np.ndarray, limit: int = 5000
) -> list[tuple[str, float]]:
    """
    Runs once, when a guest registers their selfie.

    Scans every face indexed for this event so far. At roughly 14,000 faces per
    wedding this is a few milliseconds as a sequential scan, which is why there
    is no vector index yet. Add HNSW when a single event passes ~50k faces.

    The limit is high, not a page size. The couple appear in a large fraction
    of the photographs at their own wedding, and they are the two guests most
    certain to look. A limit of a few hundred would silently truncate exactly
    the people the studio most wants happy, and truncate it in a way nothing
    reports.

    `1 - (embedding <=> :vec)` converts pgvector cosine distance to similarity.
    Both vectors are already unit length.
    """
    rows = db.execute(
        text(
            """
            SELECT f.photo_id, MAX(1 - (f.embedding <=> CAST(:vec AS vector))) AS similarity
              FROM faces f
              JOIN photos p ON p.id = f.photo_id
             WHERE f.event_id = :event_id
               AND p.status = 'done'
               AND 1 - (f.embedding <=> CAST(:vec AS vector)) > :threshold
             GROUP BY f.photo_id
             ORDER BY similarity DESC
             LIMIT :limit
            """
        ),
        {
            "vec": _vec_literal(embedding),
            "event_id": event_id,
            "threshold": settings().match_threshold,
            "limit": limit,
        },
    ).all()
    return [(r[0], float(r[1])) for r in rows]


def match_face_against_guests(
    db: Session, event_id: str, embedding: np.ndarray
) -> list[tuple[str, float]]:
    """
    Runs in the worker, for every face in every newly indexed photograph.

    This is the move that makes the whole architecture cheap. Because the worker
    writes the match rows as photographs arrive, a guest hitting refresh runs a
    plain SELECT: no model, no inference, no cost, about 5ms.

    Search-on-refresh instead would mean 150 guests x 24 refreshes = ~3,600
    extra searches per event, which roughly doubles the compute bill and spikes
    at exactly the worst moment.
    """
    rows = db.execute(
        text(
            """
            SELECT gf.guest_session_id,
                   1 - (gf.embedding <=> CAST(:vec AS vector)) AS similarity
              FROM guest_faces gf
             WHERE gf.event_id = :event_id
               AND 1 - (gf.embedding <=> CAST(:vec AS vector)) > :threshold
            """
        ),
        {
            "vec": _vec_literal(embedding),
            "event_id": event_id,
            "threshold": settings().match_threshold,
        },
    ).all()
    return [(r[0], float(r[1])) for r in rows]


def upsert_matches(
    db: Session,
    pairs: list[tuple[str, str, float]],
    studio_id: str,
    event_id: str,
) -> None:
    """
    Writes guest/photo matches, keeping the highest similarity seen.

    ON CONFLICT matters because a guest can appear in several faces of the same
    photograph, and because the worker may reprocess a photograph after a
    retry. Without it, retries would duplicate rows and a second face of the
    same person could lower a score that was already better.

    `studio_id` is stamped on every row rather than derived later. The
    row-level security policy on this table tests that column, so a row without
    it would be written and then be invisible to the studio that owns it.
    """
    if not pairs:
        return
    db.execute(
        text(
            """
            INSERT INTO guest_matches
                (guest_session_id, photo_id, studio_id, event_id, similarity, created_at)
            VALUES (:gsid, :pid, :studio, :event, :sim, now())
            ON CONFLICT (guest_session_id, photo_id)
            DO UPDATE SET similarity = GREATEST(guest_matches.similarity, EXCLUDED.similarity)
            """
        ),
        [
            {"gsid": g, "pid": p, "sim": s, "studio": studio_id, "event": event_id}
            for g, p, s in pairs
        ],
    )
