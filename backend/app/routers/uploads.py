"""
Uploads.

The rule the whole design turns on: photo bytes never pass through this API.
The client describes what it wants to send, the server authorises it and hands
back a presigned URL, the client PUTs straight to storage, then tells us it
finished. A 600 photograph round is several gigabytes, and relaying that
through a small server is how the small server falls over on the one night it
must not.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.errors import ApiError
from app.models import Job, Photo, uid
from app.routers.events import load_event
from app.security import Principal
from app.storage import get_storage

router = APIRouter(tags=["uploads"])

# Matches the contract. The uploader hides this from photographers entirely and
# simply asks again for the next slice of its own queue.
MAX_FILES_PER_CALL = 250
MAX_BYTES = 26_214_400  # 25MB

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class UploadFileIn(BaseModel):
    client_ref: str = Field(max_length=100)
    content_type: str
    size_bytes: int = Field(ge=1, le=MAX_BYTES)


class UploadsIn(BaseModel):
    files: list[UploadFileIn] = Field(min_length=1, max_length=MAX_FILES_PER_CALL)


class CompleteIn(BaseModel):
    photo_ids: list[str] = Field(min_length=1, max_length=MAX_FILES_PER_CALL)


@router.post("/events/{event_id}/uploads", status_code=201)
def request_uploads(
    event_id: str,
    body: UploadsIn,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.active_studio_db),
):
    e = load_event(db, event_id)
    if e.status != "active":
        raise ApiError(403, "EVENT_NOT_ACTIVE", "This event has not been activated.")

    storage = get_storage()
    out = []
    for f in body.files:
        ext = EXTENSIONS.get(f.content_type)
        if ext is None:
            raise ApiError(422, "UNSUPPORTED_TYPE", "That file type is not supported.")

        photo_id = uid()
        # The SERVER builds the key, always. If the client chose it, a crafted
        # value could overwrite another event's photographs, and the presigned
        # URL would authorise it because we signed exactly what we were asked
        # to sign.
        #
        # The extension comes from the declared content type rather than being
        # hardcoded, so a PNG is not stored under a name claiming to be a JPEG.
        key = f"events/{event_id}/photos/{photo_id}.{ext}"
        url, ttl = storage.signed_put_url(key, f.content_type, f.size_bytes)

        db.add(
            Photo(
                id=photo_id,
                studio_id=e.studio_id,
                event_id=event_id,
                uploaded_by=who.user_id,
                storage_key=key,
                filename=f.client_ref[:255],
                size_bytes=f.size_bytes,
                status="pending",
            )
        )
        out.append(
            {
                "client_ref": f.client_ref,
                "photo_id": photo_id,
                "upload_url": url,
                "expires_at": datetime.fromtimestamp(
                    time.time() + ttl, tz=timezone.utc
                ).isoformat(),
            }
        )
    db.commit()
    return {"uploads": out}


@router.post("/events/{event_id}/photos/complete")
def complete_uploads(
    event_id: str,
    body: CompleteIn,
    db: Session = Depends(auth.active_studio_db),
):
    """
    Marks uploads finished and enqueues them, in one transaction.

    A photograph can never end up `uploaded` without a job to process it,
    because both writes commit together or neither does. The other order,
    marking it uploaded and enqueueing afterwards, loses photographs whenever
    the process dies in between, and it dies in between exactly when the
    machine is busiest.

    Idempotent on purpose: the uploader retries on flaky venue wifi, and
    re-sending an id already past `pending` is counted, not rejected.
    """
    rows = db.scalars(
        select(Photo).where(Photo.id.in_(body.photo_ids), Photo.event_id == event_id)
    ).all()
    found = {p.id: p for p in rows}

    storage = get_storage()
    enqueued = skipped = missing = 0

    for pid in body.photo_ids:
        p = found.get(pid)
        if p is None or p.status != "pending":
            skipped += 1
            continue

        # Confirm the object is really there before promising the worker it is.
        # A client that reports success without having uploaded would otherwise
        # cost five failed attempts each, on the machine that is busy indexing a
        # live event.
        if not storage.exists(p.storage_key):
            missing += 1
            continue

        p.status = "uploaded"
        db.add(Job(studio_id=p.studio_id, event_id=p.event_id, photo_id=p.id))
        enqueued += 1

    db.commit()
    return {"enqueued": enqueued, "skipped": skipped, "missing": missing}


@router.post("/photos/{photo_id}/retry")
def retry_photo(photo_id: str, db: Session = Depends(auth.active_studio_db)):
    """Puts a failed photograph back on the queue with its attempt count reset."""
    p = db.get(Photo, photo_id)
    if p is None:
        raise ApiError(404, "NOT_FOUND", "That photograph does not exist.")
    if p.status != "failed":
        raise ApiError(409, "NOT_FAILED", "That photograph has not failed.")

    p.status = "uploaded"
    p.error_code = None

    job = db.scalar(select(Job).where(Job.photo_id == photo_id))
    if job is None:
        db.add(Job(studio_id=p.studio_id, event_id=p.event_id, photo_id=p.id))
    else:
        job.status = "pending"
        job.attempts = 0
        job.locked_at = None
        job.last_error = None

    db.commit()
    return {
        "id": p.id,
        "status": p.status,
        "thumbnail_url": None,
        "face_count": None,
        "error_code": None,
        "filename": p.filename,
        "size_bytes": p.size_bytes,
        "created_at": p.created_at.isoformat(),
        "processed_at": None,
    }
