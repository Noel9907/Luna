"""
Health, and the development stand-in for object storage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import RLS_ACTIVE, get_system_db
from app.models import EMBEDDING_DIM, MODEL_VERSION
from app.errors import ApiError
from app.storage import LocalStorage, get_storage

router = APIRouter(tags=["ops"])


def _column_dim(db: Session, table: str) -> int | None:
    """The width Postgres actually declared, e.g. `vector(512)` -> 512."""
    t = db.execute(
        text(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
              FROM pg_attribute a
             WHERE a.attrelid = CAST(:t AS regclass)
               AND a.attname = 'embedding'
               AND NOT a.attisdropped
            """
        ),
        {"t": table},
    ).scalar()
    if not t or "(" not in t:
        return None
    try:
        return int(t.split("(")[1].rstrip(")"))
    except ValueError:
        return None


@router.get("/health")
def health(db: Session = Depends(get_system_db)):
    """Liveness plus the facts that are wrong most often after a deploy."""
    db.execute(select(1))

    # FACE_BACKEND changed without running the migration, or the other way
    # round. Left alone this surfaces as a pgvector insert error on the first
    # photograph of an event, which is a bad time to find out.
    faces_dim = _column_dim(db, "faces")
    guest_dim = _column_dim(db, "guest_faces")
    ok_dim = faces_dim == EMBEDDING_DIM and guest_dim == EMBEDDING_DIM

    return {
        "ok": True,
        "env": settings().env,
        "storage": settings().storage_backend,
        "row_level_security": RLS_ACTIVE,
        "face_model": {
            "backend": settings().face_backend,
            "version": MODEL_VERSION,
            "expected_dim": EMBEDDING_DIM,
            "faces_column_dim": faces_dim,
            "guest_faces_column_dim": guest_dim,
            "schema_matches": ok_dim,
        },
    }


@router.get("/health/deep")
def health_deep(db: Session = Depends(get_system_db)):
    """
    What the monitoring checks every minute.

    `oldest_pending_seconds` across all events is the single most useful number
    in the system. If it climbs, guests at a wedding somewhere are watching a
    gallery that has stopped filling, and that is visible here minutes before
    anybody phones about it.
    """
    row = db.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE status = 'pending'),
                   count(*) FILTER (WHERE status = 'running'),
                   count(*) FILTER (WHERE status = 'failed'),
                   EXTRACT(EPOCH FROM (now() - min(created_at)
                       FILTER (WHERE status IN ('pending', 'running'))))
              FROM processing_jobs
            """
        )
    ).first()

    pending, running, failed, oldest = row if row else (0, 0, 0, None)
    return {
        "ok": True,
        "queue": {
            "pending": pending or 0,
            "running": running or 0,
            "failed": failed or 0,
            "oldest_pending_seconds": int(oldest) if oldest is not None else None,
        },
    }


# ── local object storage, development only ─────────────────────────────


@router.put("/_local-storage/{key:path}")
async def local_put(key: str, request: Request, expires: int = 0, sig: str = ""):
    """
    Stands in for a presigned R2 PUT.

    `async def`, because reading the request body is asynchronous, but the disk
    write is handed to a threadpool. Writing several megabytes on the event
    loop would stall every other request for the duration, which during a round
    of uploads means stalling all of them, repeatedly.
    """
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise ApiError(404, "NOT_FOUND", "Not available.")
    if not storage.verify(key, expires, sig):
        raise ApiError(403, "BAD_SIGNATURE", "That upload link is not valid or has expired.")

    body = await request.body()
    await run_in_threadpool(storage.write, key, body)
    return Response(status_code=200)


@router.get("/_local-storage/{key:path}")
def local_get(key: str, expires: int = 0, sig: str = ""):
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise ApiError(404, "NOT_FOUND", "Not available.")
    if not storage.verify(key, expires, sig):
        raise ApiError(403, "BAD_SIGNATURE", "That link is not valid or has expired.")
    try:
        return Response(storage.read(key), media_type="image/jpeg")
    except FileNotFoundError as exc:
        raise ApiError(404, "NOT_FOUND", "No such file.") from exc
