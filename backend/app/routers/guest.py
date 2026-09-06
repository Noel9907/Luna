"""
The guest side. Scan, consent, one selfie, then a gallery that fills itself.

A guest has no account and never will. Their entire identity is one random
token held in the phone's local storage, which is why it is stored hashed and
why consent is recorded before anything else happens.

Two connections appear here, for the reason set out in `app/db.py`. Resolving a
QR token or a session token is a bootstrap: there is no tenant until we know
which event this is. Everything after that runs on the tenant connection.
"""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, get_system_db, set_tenant
from app.errors import ApiError
from app.faces import get_engine, selfie_error
from app.matching import search_photos_for_guest, upsert_matches
from app.models import Event, GuestFace, GuestMatch, GuestSession, Studio
from app.security import fingerprint, new_opaque_token
from app.storage import get_storage

router = APIRouter(tags=["guest"])

SELFIE_MESSAGES = {
    "NO_FACE_DETECTED": "We could not find a face in that photo.",
    "MULTIPLE_FACES": "Please take a selfie with just yourself in frame.",
    "FACE_TOO_SMALL": "Please hold the camera closer.",
    "FACE_TOO_BLURRY": "That photo was too blurry. Hold still and try again.",
}

MAX_SELFIE_BYTES = 8 * 1024 * 1024

# A ceiling on one zip. Guards the worst case rather than the normal one:
# without it a single request can pin a threadpool thread for minutes while
# the photographer's uploads queue behind it.
MAX_DOWNLOAD_PHOTOS = 600


@dataclass
class GuestCtx:
    """A resolved guest, and a database session already pinned to their studio."""

    session_id: str
    event_id: str
    studio_id: str
    db: Session


def _event_by_qr(sdb: Session, qr_token: str) -> Event:
    e = sdb.scalar(select(Event).where(Event.qr_token == qr_token))
    if e is None or e.status == "draft":
        # A draft event is indistinguishable from a wrong token on purpose. The
        # QR codes are printed before the event is paid for, and a guest who
        # scans early should not be told that this specific wedding exists but
        # has not been paid for.
        raise ApiError(404, "NOT_FOUND", "This event could not be found.")
    return e


def guest_context(
    x_guest_session: str | None = Header(default=None),
    sdb: Session = Depends(get_system_db),
    db: Session = Depends(get_db),
) -> GuestCtx:
    if not x_guest_session:
        raise ApiError(401, "NO_SESSION", "No guest session.")

    gs = sdb.scalar(
        select(GuestSession).where(GuestSession.token_hash == fingerprint(x_guest_session))
    )
    if gs is None:
        raise ApiError(401, "NO_SESSION", "That session has expired.")

    set_tenant(db, gs.studio_id)
    return GuestCtx(session_id=gs.id, event_id=gs.event_id, studio_id=gs.studio_id, db=db)


# ── after consent ──────────────────────────────────────────────────────
#
# Every static /g/ path is registered BEFORE the dynamic /g/{qr_token} at the
# bottom of this file. FastAPI matches routes in registration order, so if
# /g/{qr_token} came first it would swallow "photos", "selfie" and "session"
# as QR tokens and every one of these would 404 in a way that looks like a
# database problem rather than a routing one.


@router.post("/g/selfie")
def guest_selfie(
    selfie: UploadFile = File(...),
    ctx: GuestCtx = Depends(guest_context),
):
    """
    Runs once per guest. After this, matching happens in the worker.

    Declared `def`, not `async def`, and that one keyword is the difference
    between a working reception and a stalled one. Detection and embedding are
    roughly 350ms of straight CPU work. Inside an `async def` that runs on the
    event loop and blocks every other request in the process, so forty guests
    scanning the QR in the same five minutes would freeze the photographer's
    uploads. A plain `def` is handed to FastAPI's threadpool instead, and the
    loop stays free.
    """
    db = ctx.db
    gs = db.get(GuestSession, ctx.session_id)
    if gs is None:
        raise ApiError(401, "NO_SESSION", "That session has expired.")

    # Counted before the work is done, and committed immediately, so a caller
    # looping on failures still exhausts their allowance. Charging only for
    # successes would leave the expensive path free.
    if gs.selfie_attempts >= settings().selfie_max_attempts:
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many attempts. Ask the photographer.")
    gs.selfie_attempts += 1
    db.commit()

    raw = selfie.file.read(MAX_SELFIE_BYTES + 1)
    if len(raw) > MAX_SELFIE_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "That photo is too large.")

    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ApiError(422, "DECODE_FAILED", "We could not read that photo.")

    # No blur floor here: selfie_error runs its own stricter gate and has to
    # classify the failure rather than silently drop the face.
    engine = get_engine(
        settings().min_face_px,
        settings().min_detect_score,
        backend=settings().face_backend,
    )

    # Classify the failure. Selfie rejection is the most common thing a guest
    # will ever see go wrong, and a generic message makes the whole product
    # feel broken rather than telling them to move into better light.
    problem, stats = selfie_error(engine, img)
    if problem:
        print(f"  selfie rejected: {problem} {stats}", flush=True)
        raise ApiError(422, problem, SELFIE_MESSAGES[problem])
    print(f"  selfie accepted: {stats}", flush=True)

    face = engine.embed_single(img)
    if face is None:
        raise ApiError(422, "NO_FACE_DETECTED", SELFIE_MESSAGES["NO_FACE_DETECTED"])

    # Replacing rather than adding: a guest retaking their selfie means the
    # previous one was wrong, and keeping both would match them against a face
    # they explicitly rejected.
    db.query(GuestFace).filter(GuestFace.guest_session_id == gs.id).delete()
    db.add(
        GuestFace(
            studio_id=ctx.studio_id,
            event_id=ctx.event_id,
            guest_session_id=gs.id,
            embedding=face.embedding.tolist(),
        )
    )
    db.flush()

    # The catch-up search, over everything already indexed. From here the
    # worker takes over and this never runs again for this guest.
    hits = search_photos_for_guest(db, ctx.event_id, face.embedding)
    upsert_matches(
        db,
        [(gs.id, pid, sim) for pid, sim in hits],
        studio_id=ctx.studio_id,
        event_id=ctx.event_id,
    )
    db.commit()

    return {"matched_count": len(hits)}


@router.get("/g/photos")
def guest_photos(
    after: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=200),
    ctx: GuestCtx = Depends(guest_context),
):
    """
    A plain database read. No model, no external call, no per-request cost.

    That is the entire reason this architecture works: a guest polling every
    twenty seconds for six hours costs a handful of indexed lookups.

    Filtering and paging happen in SQL, not in Python. The couple appear in
    thousands of photographs and are the two people most likely to keep the
    page open all night; loading every match row on each poll in order to
    return the four that are new would make their phone the slowest one at the
    wedding.
    """
    db = ctx.db
    storage = get_storage()

    # "<timestamp>|<photo id>". Split rather than validated: a malformed cursor
    # is treated as no cursor, which shows the first page again instead of
    # failing. A guest cannot fix a 400 here.
    cursor_ts: str | None = None
    cursor_id: str | None = None
    if cursor and "|" in cursor:
        cursor_ts, _, cursor_id = cursor.partition("|")

    rows = db.execute(
        text(
            """
            SELECT p.id, p.thumb_key, p.storage_key, p.width, p.height, m.created_at
              FROM guest_matches m
              JOIN photos p ON p.id = m.photo_id
             WHERE m.guest_session_id = :gs
               AND p.status = 'done'
               -- Cast both sides: Postgres cannot type a parameter that
               -- appears only in an IS NULL test.
               AND (CAST(:after AS timestamptz) IS NULL
                    OR m.created_at > CAST(:after AS timestamptz))
               -- Keyset on (created_at, photo_id), NOT on created_at alone.
               -- The catch-up search writes every one of a guest's matches in
               -- one transaction, so they all share a timestamp; paging on it
               -- alone asks for rows strictly older than a value every row has,
               -- returns nothing, and silently caps the gallery at one page.
               AND (CAST(:cursor_ts AS timestamptz) IS NULL
                    OR (m.created_at, p.id)
                        < (CAST(:cursor_ts AS timestamptz), CAST(:cursor_id AS uuid)))
             ORDER BY m.created_at DESC, p.id DESC
             LIMIT :limit
            """
        ),
        {
            "gs": ctx.session_id,
            "after": after,
            "cursor_ts": cursor_ts,
            "cursor_id": cursor_id,
            "limit": limit,
        },
    ).all()

    totals = db.execute(
        text(
            """
            SELECT count(*), max(m.created_at)
              FROM guest_matches m
              JOIN photos p ON p.id = m.photo_id
             WHERE m.guest_session_id = :gs AND p.status = 'done'
            """
        ),
        {"gs": ctx.session_id},
    ).first()
    total_count = totals[0] if totals else 0
    newest = totals[1] if totals else None

    # When watermarking is on the worker wrote a marked copy beside the original,
    # and that is what a guest is given. Keyed by convention rather than stored,
    # so this needs no column and no migration. Photographs indexed before the
    # setting was turned on have no such copy, so turning it on mid-event means
    # re-queueing them.
    marked = bool(settings().watermark_text)

    items = [
        {
            "photo_id": str(r[0]),
            # The thumbnail is what the grid loads. The full image is only
            # fetched when a guest opens one photograph full screen.
            "thumbnail_url": storage.signed_get_url(r[1] or r[2]),
            "full_url": storage.signed_get_url(
                f"events/{ctx.event_id}/display/{r[0]}.jpg" if marked else r[2]
            ),
            "width": r[3],
            "height": r[4],
            "matched_at": r[5].isoformat(),
        }
        for r in rows
    ]

    return {
        "items": items,
        # Carries the tiebreaker too, or the next page cannot be located.
        "next_cursor": (
            f"{rows[-1][5].isoformat()}|{rows[-1][0]}" if len(rows) == limit else None
        ),
        "latest_cursor": (newest or datetime.now(timezone.utc)).isoformat(),
        "total_count": total_count,
    }


@router.get("/g/download")
def guest_download_all(ctx: GuestCtx = Depends(guest_context)):
    """
    Every photograph this guest appears in, as one zip.

    Serves the watermarked copies when watermarking is on, exactly as the
    gallery does. A guest is never handed the clean original.

    ZIP_STORED, not DEFLATE. These are JPEGs: they are already compressed, so
    deflating them burns CPU on every request to save almost nothing. Storing
    makes the zip a copy rather than a computation.

    Spooled to disk past a few megabytes rather than held in memory. A guest in
    a thousand photographs is half a gigabyte, and several of them at once would
    otherwise be enough to take the process down during a reception.
    """
    db = ctx.db
    storage = get_storage()
    marked = bool(settings().watermark_text)

    rows = db.execute(
        text(
            """
            SELECT p.id, p.storage_key, p.filename
              FROM guest_matches m
              JOIN photos p ON p.id = m.photo_id
             WHERE m.guest_session_id = :gs AND p.status = 'done'
             ORDER BY m.created_at DESC, p.id DESC
             LIMIT :cap
            """
        ),
        {"gs": ctx.session_id, "cap": MAX_DOWNLOAD_PHOTOS},
    ).all()

    if not rows:
        raise ApiError(404, "NOTHING_TO_DOWNLOAD", "There is nothing to download yet.")

    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    written = 0
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_STORED) as zf:
        for i, (photo_id, storage_key, filename) in enumerate(rows, start=1):
            key = f"events/{ctx.event_id}/display/{photo_id}.jpg" if marked else storage_key
            try:
                data = storage.read(key)
            except Exception:  # noqa: BLE001
                # One missing object must not cost the guest the other 999.
                # Happens when watermarking was switched on after indexing.
                continue
            zf.writestr(f"photographs/{i:04d}.jpg", data)
            written += 1

    if written == 0:
        # Matches exist but not one file could be read. In practice this means
        # watermarking was switched on after these photographs were indexed, so
        # the display copies were never written. Logged loudly because the guest
        # sees a generic message and the cause is entirely operational.
        print(
            f"  DOWNLOAD EMPTY: {len(rows)} matches, 0 readable. "
            f"watermark={'on' if marked else 'off'}. "
            "If on, re-index: the display/ copies are made at index time.",
            flush=True,
        )
        raise ApiError(
            503, "DOWNLOAD_UNAVAILABLE", "Downloads are not ready yet. Try again shortly."
        )

    size = spool.tell()
    spool.seek(0)
    return StreamingResponse(
        spool,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="photographs.zip"',
            "Content-Length": str(size),
        },
    )


@router.delete("/g/session", status_code=204)
def guest_delete(ctx: GuestCtx = Depends(guest_context)):
    """
    The DPDP deletion path. Immediate, not scheduled.

    Removes the embedding, the matches and the session. Does not touch the
    studio's photographs: the guest has no rights over those, only over the
    link between themselves and them.
    """
    db = ctx.db
    db.query(GuestFace).filter(GuestFace.guest_session_id == ctx.session_id).delete()
    db.query(GuestMatch).filter(GuestMatch.guest_session_id == ctx.session_id).delete()
    db.query(GuestSession).filter(GuestSession.id == ctx.session_id).delete()
    db.commit()
    return Response(status_code=204)


# ── before consent ─────────────────────────────────────────────────────
#
# Registered last, because these are the only /g/ routes with a variable in the
# path. See the note above the selfie route.


@router.get("/g/{qr_token}")
def guest_event(qr_token: str, sdb: Session = Depends(get_system_db)):
    """
    Everything a stranger holding the token may see, and nothing more.

    No photo counts, no guest numbers, no studio internals. The QR code sits on
    a table at a wedding and will be photographed and shared.
    """
    e = _event_by_qr(sdb, qr_token)
    studio = sdb.get(Studio, e.studio_id)
    branded = e.branding_mode == "studio" and studio is not None

    logo_url = None
    if branded and studio.brand_logo_key:
        logo_url = get_storage().signed_get_url(studio.brand_logo_key, seconds=86400)

    return {
        "event_name": e.name,
        "event_date": e.event_date,
        "accepting_guests": e.status == "active",
        "face_retention_days": e.face_retention_days,
        "branding": {
            "mode": e.branding_mode,
            "studio_name": studio.name if branded else None,
            "logo_url": logo_url,
            "brand_color": studio.brand_color if branded else None,
        },
    }


class ConsentIn(BaseModel):
    consented: bool


@router.post("/g/{qr_token}/session", status_code=201)
def open_session(qr_token: str, body: ConsentIn, sdb: Session = Depends(get_system_db)):
    """
    Records consent and opens a session.

    There is no path to a session without explicit consent, because everything
    after this is biometric data under the DPDP Act.

    The retention window is COPIED onto the session row rather than referenced.
    What this particular guest agreed to becomes a fact about their row, so a
    studio's agreement being renegotiated next month cannot change what someone
    was told at a wedding last month.
    """
    if not body.consented:
        raise ApiError(422, "CONSENT_REQUIRED", "Consent is required.")

    e = _event_by_qr(sdb, qr_token)
    if e.status != "active":
        raise ApiError(403, "EVENT_NOT_ACTIVE", "This event is not accepting guests.")

    existing = sdb.scalar(
        select(func.count()).select_from(GuestSession).where(GuestSession.event_id == e.id)
    )
    if (existing or 0) >= settings().guest_sessions_per_event:
        raise ApiError(429, "EVENT_FULL", "This event has reached its guest limit.")

    raw = new_opaque_token()
    gs = GuestSession(
        studio_id=e.studio_id,
        event_id=e.id,
        token_hash=fingerprint(raw),
        consent_face_retention_days=e.face_retention_days,
    )
    sdb.add(gs)
    sdb.commit()

    # A date, not a duration. The consent screen shows this, so the promise is
    # specific and checkable rather than a general claim about policy.
    deletion_date = (
        (datetime.now(timezone.utc) + timedelta(days=e.face_retention_days)).date().isoformat()
    )

    return {
        "session_token": raw,
        "expires_at": None,
        "face_retention_days": e.face_retention_days,
        "face_deletion_date": deletion_date,
    }
