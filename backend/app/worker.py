"""
The processing worker.

    python -m app.worker

Run several for more throughput. They coordinate through Postgres, so there is
nothing to configure and no broker to operate.

    for i in 1 2 3; do python -m app.worker & done

Each worker uses both connections described in `app/db.py`, and the split is
deliberate. Claiming a job is cross-tenant by nature: the worker serves every
studio, so it must be able to see every studio's queue. Everything after the
claim is pinned to the one studio that job belongs to, which means a mistake in
the matching code cannot write a face row into somebody else's wedding.
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import system_session, tenant_session
from app.faces import get_engine
from app.matching import match_face_against_guests, upsert_matches
from app.models import Face, Job, Photo

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False
    print("\n  stopping after current job")


# ── claiming ───────────────────────────────────────────────────────────

CLAIM_SQL = text(
    """
    WITH claimed AS (
        UPDATE processing_jobs j
           SET status = 'running', locked_at = now(), attempts = j.attempts + 1
         WHERE j.id = (
             SELECT pj.id
               FROM processing_jobs pj
               JOIN events e ON e.id = pj.event_id
              WHERE pj.status = 'pending'
                 OR (pj.status = 'running' AND pj.locked_at < :stale)
              ORDER BY e.last_job_claimed_at ASC NULLS FIRST, pj.created_at ASC
              FOR UPDATE OF pj SKIP LOCKED
              LIMIT 1
         )
        RETURNING j.id, j.studio_id, j.event_id, j.photo_id, j.attempts
    ), served AS (
        UPDATE events
           SET last_job_claimed_at = now()
         WHERE id IN (SELECT event_id FROM claimed)
    )
    SELECT id, studio_id, event_id, photo_id, attempts FROM claimed
    """
)


def claim_job(db: Session) -> tuple[str, str, str, str, int] | None:
    """
    Takes one job, atomically, and fairly.

    Three separate ideas are packed into that statement.

    FOR UPDATE SKIP LOCKED is what removes the need for a broker. Each worker
    locks a different row and skips anything already locked, so N workers take
    N different jobs instead of queueing behind the same one.

    The `locked_at < :stale` arm is crash recovery. A worker that dies holding
    a job releases it after a few minutes, so a power cut during a reception
    costs minutes rather than the rest of the night. `attempts` increments here,
    at claim time rather than at failure time, so a job that kills the process
    outright still burns an attempt and cannot loop forever.

    The ordering is the fair share, and it is the part worth understanding.
    Ordering by `created_at` alone is FIFO, and FIFO starves. Picture a
    Saturday: studio A is shooting a wedding right now and uploads sixty
    photographs every ten minutes, while studio B bulk uploads a fifteen
    thousand photograph archive from last week. Every one of B's jobs is older
    than A's next round, so under FIFO A's guests see nothing new for half an
    hour, in the middle of the reception, while a week-old backlog nobody is
    waiting on takes the whole machine.

    So the queue is ordered by `events.last_job_claimed_at`, oldest first, with
    never-served events ahead of everything. Whoever waited longest goes next,
    and taking a job immediately marks that event as just served, so the next
    claim goes to somebody else. That is round robin between events, with FIFO
    inside each one, and it is why A's three jobs and B's fifteen thousand
    interleave instead of queueing.

    An attempt that looks right and is not: ranking each job by its position
    within its own event, `row_number() OVER (PARTITION BY event_id ORDER BY
    created_at)`, and ordering by that. It reads correctly and it fails
    completely. The ranking is recomputed on every claim over only the jobs
    still pending, so the moment B's first job is taken, B's second job becomes
    position 1 again. Two events then permanently tie at position 1 and the
    tiebreak is age, which B wins every single time, because B's backlog is
    older. The queue behaves exactly like FIFO while looking like fair
    scheduling. Positions have to accumulate to be fair, and a position
    recomputed from scratch each round cannot accumulate. A served-at timestamp
    on the event does accumulate, which is the whole reason it lives there.

    The unit is the event, not the studio, on purpose. Starvation is felt by
    guests standing at one particular wedding, and a studio shooting two
    weddings on the same Saturday would otherwise let the morning one starve
    the evening one.

    Two workers claiming at the same instant can both read the same
    `last_job_claimed_at` and both pick the same event. That is a fairness
    wobble, not a correctness bug: SKIP LOCKED still hands them different rows,
    and the imbalance is one job wide and corrects on the next claim.
    """
    stale = datetime.now(timezone.utc) - timedelta(minutes=settings().job_stale_minutes)
    row = db.execute(CLAIM_SQL, {"stale": stale}).first()
    db.commit()
    if row is None:
        return None
    return (str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]))


# ── image work ─────────────────────────────────────────────────────────


def make_thumbnail(image_bgr: np.ndarray) -> bytes:
    """
    A small JPEG for the guest gallery.

    Made here, once, rather than served from the original. A guest scrolling
    forty photographs on venue wifi would otherwise pull forty full-size
    frames, which is tens of megabytes for a grid of images displayed a few
    hundred pixels wide. This costs about 30ms per photograph on the server and
    saves every guest, on every scroll, for the life of the gallery.

    INTER_AREA rather than the default, because it is the correct filter for
    shrinking and does not produce the aliasing that makes downscaled faces
    look strange.
    """
    s = settings()
    h, w = image_bgr.shape[:2]
    scale = min(1.0, s.thumb_long_edge / max(h, w))
    small = (
        cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else image_bgr
    )
    if s.watermark_text:
        small = draw_watermark(small, s.watermark_text)
    ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), s.thumb_quality])
    if not ok:
        raise ValueError("THUMBNAIL_FAILED")
    return buf.tobytes()


def draw_watermark(image_bgr: np.ndarray, text: str) -> np.ndarray:
    """
    Burn the studio's mark into the lower left corner.

    Sized from the image width rather than fixed, so it reads the same on a
    640px thumbnail and a 4000px frame. Drawn twice, dark then light, because a
    single colour disappears against either a white saree or a dark hall.
    """
    out = image_bgr.copy()
    h, w = out.shape[:2]
    # Tuned so the mark reads at roughly the same physical size on a 640px
    # thumbnail and a 2400px frame. Smaller than this and it vanishes in the
    # grid, which defeats the point of having it.
    scale = max(0.5, w / 750)
    weight = max(1, round(scale * 1.6))
    margin = max(8, int(w * 0.025))
    font = cv2.FONT_HERSHEY_SIMPLEX
    origin = (margin, h - margin)

    cv2.putText(out, text, origin, font, scale, (0, 0, 0), weight + 2, cv2.LINE_AA)
    cv2.putText(out, text, origin, font, scale, (255, 255, 255), weight, cv2.LINE_AA)

    # Blended rather than drawn straight on, so it marks the photograph without
    # competing with the faces in it.
    a = settings().watermark_opacity
    return cv2.addWeighted(out, a, image_bgr, 1.0 - a, 0.0)


def make_display(image_bgr: np.ndarray) -> bytes:
    """The full-size copy a guest opens, watermarked. The original is untouched."""
    marked = draw_watermark(image_bgr, settings().watermark_text)
    ok, buf = cv2.imencode(".jpg", marked, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise ValueError("THUMBNAIL_FAILED")
    return buf.tobytes()


def thumb_key_for(event_id: str, photo_id: str) -> str:
    return f"events/{event_id}/thumbs/{photo_id}.jpg"


def display_key_for(event_id: str, photo_id: str) -> str:
    return f"events/{event_id}/display/{photo_id}.jpg"


# ── processing ─────────────────────────────────────────────────────────


def process(db: Session, job_id: str, event_id: str, photo_id: str) -> Photo:
    """
    Index one photograph, inside one transaction.

    Everything here runs on a connection pinned to this job's studio, so the
    queries below are not merely filtered by tenant, they are incapable of
    reaching another one.
    """
    from app.storage import get_storage

    engine = get_engine(
        settings().min_face_px,
        settings().min_detect_score,
        settings().index_min_blur,
        settings().face_backend,
    )
    storage = get_storage()

    photo = db.get(Photo, photo_id)
    job = db.get(Job, job_id)
    if photo is None:
        # The photograph was deleted while queued. Nothing to do, and not a
        # failure: retrying would never succeed.
        if job is not None:
            job.status = "done"
        db.commit()
        return None  # type: ignore[return-value]

    photo.status = "processing"
    db.commit()

    data = storage.read(photo.storage_key)
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("CORRUPT_IMAGE")

    photo.height, photo.width = img.shape[:2]

    thumb_key = thumb_key_for(event_id, photo.id)
    storage.write(thumb_key, make_thumbnail(img), "image/jpeg")
    photo.thumb_key = thumb_key

    # A watermarked full-size copy, written beside the original rather than over
    # it, so the mark can be removed later without re-uploading anything.
    if settings().watermark_text:
        storage.write(display_key_for(event_id, photo.id), make_display(img), "image/jpeg")

    found = engine.detect_and_embed(img)

    # Idempotency. A job can run twice after a crash or a retry, and without
    # this the same photograph accumulates duplicate faces every time.
    db.query(Face).filter(Face.photo_id == photo.id).delete()

    match_pairs: list[tuple[str, str, float]] = []
    for f in found:
        db.add(
            Face(
                studio_id=photo.studio_id,
                event_id=photo.event_id,
                photo_id=photo.id,
                embedding=f.embedding.tolist(),
                bbox_x=f.bbox[0],
                bbox_y=f.bbox[1],
                bbox_w=f.bbox[2],
                bbox_h=f.bbox[3],
                det_score=f.det_score,
                blur_score=f.blur,
            )
        )
        # Match against guests who already registered, right now, so their
        # gallery updates without ever running a model on their request.
        for gsid, sim in match_face_against_guests(db, photo.event_id, f.embedding):
            match_pairs.append((gsid, photo.id, sim))

    upsert_matches(db, match_pairs, studio_id=photo.studio_id, event_id=photo.event_id)

    photo.face_count = len(found)
    photo.status = "done"
    photo.error_code = None
    photo.processed_at = datetime.now(timezone.utc)
    if job is not None:
        job.status = "done"
        job.last_error = None

    # One commit: the photograph is either fully indexed or untouched. A
    # half-indexed photograph, with some faces written and some not, would
    # match some guests and silently miss others forever.
    db.commit()
    return photo


# Failures that will never succeed on a retry. A file that does not decode
# decodes no better the fifth time, and those four extra attempts are spent on
# a machine that is busy indexing a live event.
PERMANENT_ERRORS = frozenset({"CORRUPT_IMAGE", "THUMBNAIL_FAILED", "UNSUPPORTED_IMAGE"})


def record_failure(db: Session, job_id: str, photo_id: str, exc: Exception, attempts: int) -> bool:
    """Returns True when the job has been given up on."""
    job = db.get(Job, job_id)
    photo = db.get(Photo, photo_id)
    if job is None:
        return True

    # Errors raised as bare uppercase strings are our own classifications and
    # are safe to show a studio. Anything else is an internal detail.
    code = str(exc) if str(exc).isupper() and " " not in str(exc) else "PROCESSING_FAILED"
    job.last_error = f"{type(exc).__name__}: {exc}"[:500]

    if code in PERMANENT_ERRORS or attempts >= settings().max_attempts:
        job.status = "failed"
        if photo is not None:
            photo.status = "failed"
            photo.error_code = code
        db.commit()
        return True

    # Back to pending. The stale-lock arm of the claim query picks it up again.
    job.status = "pending"
    job.locked_at = None
    db.commit()
    return False


# ── loop ───────────────────────────────────────────────────────────────


def run() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print("  worker up. waiting for jobs. ctrl-c to stop.")
    # load models once
    get_engine(
        settings().min_face_px,
        settings().min_detect_score,
        settings().index_min_blur,
        settings().face_backend,
    )

    while _running:
        with system_session() as sdb:
            claimed = claim_job(sdb)

        if claimed is None:
            time.sleep(settings().worker_poll_seconds)
            continue

        job_id, studio_id, event_id, photo_id, attempts = claimed
        started = time.time()

        with tenant_session(studio_id) as db:
            try:
                photo = process(db, job_id, event_id, photo_id)
                if photo is not None:
                    label = photo.filename or photo.id[:8]
                    print(
                        f"  done  {label:<28} {photo.face_count} face(s)  "
                        f"{time.time() - started:.2f}s"
                    )
            except Exception as exc:  # noqa: BLE001 - one bad photograph must not stop the queue
                db.rollback()
                gave_up = record_failure(db, job_id, photo_id, exc, attempts)
                verb = "FAIL " if gave_up else "retry"
                print(f"  {verb} {photo_id[:8]}  attempt {attempts}: {exc}")

    print("  worker stopped")


if __name__ == "__main__":
    sys.exit(run())
