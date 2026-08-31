"""
Drives the whole API in one process and checks it behaves.

    python scripts/prove_api_flow.py

Needs no running server, no worker and no photographs: it exercises the paths
that have nothing to do with faces, because face matching is already proven by
`prove_matching.py` and `smoke_test.py`. What is checked here is everything
built around that loop, and every one of these is a rule that would be
expensive to get wrong at a real wedding:

    an admin can create a studio, and the owner's password works once
    a new account must change its password before it can do anything
    a draft event refuses uploads
    only the webhook activates an event, never the browser
    a replayed webhook does not activate or charge twice
    a webhook whose signature does not match is refused
    a wrong amount does not activate the event
    a guest cannot open a session without consenting
    a studio cannot see another studio's events through the API
    login rate limiting engages

It runs against whatever DATABASE_URL points at and cleans up after itself.
"""

from __future__ import annotations

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from app import razorpay  # noqa: E402
from app.db import system_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Payment, User  # noqa: E402
from app.routers.studio import generate_password  # noqa: E402
from app.storage import get_storage  # noqa: E402
from app.security import hash_password  # noqa: E402

PASS = "  ok   "
FAIL = "  FAIL "

failures: list[str] = []


def check(condition: bool, description: str, detail: str = "") -> None:
    if condition:
        print(f"{PASS} {description}")
    else:
        failures.append(description)
        print(f"{FAIL} {description}{('  <- ' + detail) if detail else ''}")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def tiny_jpeg() -> bytes:
    """
    A real, decodable JPEG.

    Not `b"x" * 32`. Junk bytes are enqueued like any other photograph, and a
    worker running against the same database then picks them up and fails to
    decode them, five times each, polluting its log with failures that belong
    to a test.
    """
    import cv2
    import numpy as np

    img = np.full((64, 64, 3), 200, np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        raise RuntimeError("could not encode the test image")
    return buf.tobytes()


def main() -> int:
    marker = uuid.uuid4().hex[:8]
    client = TestClient(app, raise_server_exceptions=False)

    # ── bootstrap a platform admin directly, the way the script does ────
    admin_user = f"admin{marker}"
    admin_password = generate_password()
    with system_session() as db:
        db.add(
            User(
                studio_id=None,
                username=admin_user,
                name="Test Admin",
                password_hash=hash_password(admin_password),
                role="platform_admin",
                status="active",
                must_change_password=False,
            )
        )
        db.commit()

    studio_ids: list[str] = []
    try:
        r = client.post(
            "/v1/auth/login", json={"identifier": admin_user, "password": admin_password}
        )
        check(r.status_code == 200, "platform admin can sign in", r.text[:120])
        admin_token = r.json()["access_token"]

        # ── admin creates a studio and its owner ────────────────────────
        r = client.post(
            "/v1/admin/studios",
            headers=bearer(admin_token),
            json={
                "name": f"Studio {marker}",
                "phone": "+91 98470 00000",
                "owner_username": f"owner{marker}",
                "owner_name": "Owner One",
                "face_retention_days": 30,
            },
        )
        check(r.status_code == 201, "admin creates a studio with an owner", r.text[:160])
        created = r.json()
        studio_ids.append(created["id"])
        owner_password = created["initial_password"]
        owner_user = created["owner_username"]

        # ── retention cap is enforced ───────────────────────────────────
        r = client.post(
            "/v1/admin/studios",
            headers=bearer(admin_token),
            json={
                "name": f"TooLong {marker}",
                "owner_username": f"toolong{marker}",
                "face_retention_days": 4000,
            },
        )
        check(
            r.status_code == 422 and r.json()["error"]["code"] == "RETENTION_OUT_OF_RANGE",
            "face retention above the platform ceiling is refused",
            r.text[:120],
        )

        # ── the owner must change their password first ──────────────────
        r = client.post(
            "/v1/auth/login", json={"identifier": owner_user, "password": owner_password}
        )
        check(r.status_code == 200, "owner signs in with the generated password")
        owner_token = r.json()["access_token"]

        r = client.get("/v1/events", headers=bearer(owner_token))
        check(
            r.status_code == 403 and r.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED",
            "an account that must change its password is blocked from everything else",
            r.text[:120],
        )

        new_password = "a-much-better-password"
        r = client.post(
            "/v1/auth/password",
            headers=bearer(owner_token),
            json={"current_password": owner_password, "new_password": new_password},
        )
        check(r.status_code == 200, "owner changes password and gets a fresh token pair")
        owner_token = r.json()["access_token"]
        owner_refresh = r.json()["refresh_token"]

        r = client.get("/v1/events", headers=bearer(owner_token))
        check(r.status_code == 200, "owner can work after changing the password", r.text[:120])

        # ── refresh rotation detects reuse ──────────────────────────────
        r = client.post("/v1/auth/refresh", json={"refresh_token": owner_refresh})
        check(r.status_code == 200, "a refresh token can be redeemed once")
        rotated = r.json()["refresh_token"]

        r = client.post("/v1/auth/refresh", json={"refresh_token": owner_refresh})
        check(
            r.status_code == 401,
            "redeeming the same refresh token twice is refused",
            r.text[:120],
        )
        r = client.post("/v1/auth/refresh", json={"refresh_token": rotated})
        check(
            r.status_code == 401,
            "reuse revokes the whole family, so the thief's successor dies too",
            r.text[:120],
        )

        r = client.post(
            "/v1/auth/login", json={"identifier": owner_user, "password": new_password}
        )
        owner_token = r.json()["access_token"]

        # ── a draft event refuses uploads ───────────────────────────────
        r = client.post(
            "/v1/events",
            headers=bearer(owner_token),
            json={"name": "Wedding One", "event_date": "2026-09-12", "tier_code": "pro"},
        )
        check(r.status_code == 201, "owner creates an event", r.text[:160])
        event = r.json()
        check(event["status"] == "draft", "a new event starts as draft, not active")
        event_id = event["id"]

        r = client.post(
            f"/v1/events/{event_id}/uploads",
            headers=bearer(owner_token),
            json={"files": [{"client_ref": "a", "content_type": "image/jpeg", "size_bytes": 1000}]},
        )
        check(
            r.status_code == 403 and r.json()["error"]["code"] == "EVENT_NOT_ACTIVE",
            "a draft event refuses uploads",
            r.text[:120],
        )

        # ── a guest cannot reach a draft event ──────────────────────────
        qr_token = event["qr_url"].rsplit("/", 1)[-1]
        r = client.get(f"/v1/g/{qr_token}")
        check(r.status_code == 404, "a draft event is invisible to guests", r.text[:120])

        # ── checkout, then the webhook ──────────────────────────────────
        r = client.post(f"/v1/events/{event_id}/checkout", headers=bearer(owner_token))
        check(r.status_code == 201, "checkout creates an order", r.text[:160])
        order_id = r.json()["razorpay_order_id"]
        amount = r.json()["amount_paise"]
        check(amount == 300000, "the Pro tier is charged at 3,000 rupees")

        r2 = client.post(f"/v1/events/{event_id}/checkout", headers=bearer(owner_token))
        check(
            r2.json()["razorpay_order_id"] == order_id,
            "calling checkout again returns the same order rather than charging twice",
        )

        r = client.get(f"/v1/events/{event_id}", headers=bearer(owner_token))
        check(r.json()["status"] == "draft", "creating an order does not activate the event")

        # A tampered signature must be refused.
        raw = razorpay.fake_webhook_payload(order_id, f"pay_ok{marker}", amount, "upi")
        r = client.post(
            "/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Signature": "0" * 64},
        )
        check(r.status_code == 400, "a webhook with a bad signature is refused", r.text[:120])

        r = client.get(f"/v1/events/{event_id}", headers=bearer(owner_token))
        check(r.json()["status"] == "draft", "a refused webhook does not activate the event")

        # The wrong amount must not activate it either.
        wrong = razorpay.fake_webhook_payload(order_id, f"pay_bad{marker}", 100, "upi")
        client.post(
            "/v1/webhooks/razorpay",
            content=wrong,
            headers={"X-Razorpay-Signature": razorpay.sign_fake_webhook(wrong)},
        )
        r = client.get(f"/v1/events/{event_id}", headers=bearer(owner_token))
        check(
            r.json()["status"] == "draft",
            "a webhook for the wrong amount does not activate the event",
        )

        # The real one.
        sig = razorpay.sign_fake_webhook(raw)
        r = client.post(
            "/v1/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
        )
        check(r.status_code == 200, "a correctly signed webhook is accepted", r.text[:120])

        r = client.get(f"/v1/events/{event_id}", headers=bearer(owner_token))
        check(r.json()["status"] == "active", "the webhook activates the event", r.text[:160])

        # Redelivery must be a no-op.
        r = client.post(
            "/v1/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
        )
        check(r.json().get("duplicate") is True, "a redelivered webhook is recognised and dropped")

        with system_session() as db:
            paid = db.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.order_id == order_id, Payment.status == "captured")
            )
        check(paid == 1, "redelivery does not create a second captured payment", f"count={paid}")

        # ── uploads work once active ────────────────────────────────────
        r = client.post(
            f"/v1/events/{event_id}/uploads",
            headers=bearer(owner_token),
            json={
                "files": [
                    {"client_ref": "one", "content_type": "image/jpeg", "size_bytes": 2048},
                    {"client_ref": "two", "content_type": "image/png", "size_bytes": 2048},
                ]
            },
        )
        check(r.status_code == 201, "an active event issues upload URLs", r.text[:160])
        uploads = r.json()["uploads"]
        check(
            uploads[0]["upload_url"].split("?")[0].endswith(".jpg")
            and uploads[1]["upload_url"].split("?")[0].endswith(".png"),
            "the object key extension follows the declared content type",
        )

        # Completing without having uploaded must not enqueue anything.
        r = client.post(
            f"/v1/events/{event_id}/photos/complete",
            headers=bearer(owner_token),
            json={"photo_ids": [u["photo_id"] for u in uploads]},
        )
        check(
            r.json()["enqueued"] == 0 and r.json()["missing"] == 2,
            "completing an upload that never happened enqueues nothing",
            r.text[:160],
        )

        # Now actually PUT the bytes, then complete.
        jpeg = tiny_jpeg()
        for u in uploads:
            put = client.put(u["upload_url"].replace("http://localhost:8000", ""), content=jpeg)
            check(put.status_code == 200, f"presigned PUT accepted for {u['client_ref']}")

        r = client.post(
            f"/v1/events/{event_id}/photos/complete",
            headers=bearer(owner_token),
            json={"photo_ids": [u["photo_id"] for u in uploads]},
        )
        check(r.json()["enqueued"] == 2, "completing real uploads enqueues them", r.text[:160])

        r = client.post(
            f"/v1/events/{event_id}/photos/complete",
            headers=bearer(owner_token),
            json={"photo_ids": [u["photo_id"] for u in uploads]},
        )
        check(
            r.json()["enqueued"] == 0 and r.json()["skipped"] == 2,
            "re-completing the same ids is a no-op, not an error",
        )

        # ── guest consent ───────────────────────────────────────────────
        r = client.get(f"/v1/g/{qr_token}")
        check(r.status_code == 200, "an active event is visible to guests")
        check(
            r.json()["face_retention_days"] == 30,
            "the guest is told the event's actual face retention",
        )

        r = client.post(f"/v1/g/{qr_token}/session", json={"consented": False})
        check(
            r.status_code == 422 and r.json()["error"]["code"] == "CONSENT_REQUIRED",
            "there is no path to a session without consent",
        )

        r = client.post(f"/v1/g/{qr_token}/session", json={"consented": True})
        check(r.status_code == 201, "consenting opens a session", r.text[:120])
        body = r.json()
        check(
            "face_deletion_date" in body and len(body["face_deletion_date"]) == 10,
            "the consent response carries a real deletion date, not just a duration",
        )
        guest_token = body["session_token"]

        r = client.get("/v1/g/photos", headers={"X-Guest-Session": guest_token})
        check(r.status_code == 200 and r.json()["total_count"] == 0, "a new guest has no photos")

        r = client.get("/v1/g/photos", headers={"X-Guest-Session": "not-a-real-token"})
        check(r.status_code == 401, "a forged guest token is refused")

        # ── a second studio cannot see the first ────────────────────────
        r = client.post(
            "/v1/admin/studios",
            headers=bearer(admin_token),
            json={"name": f"Rival {marker}", "owner_username": f"rival{marker}"},
        )
        rival = r.json()
        studio_ids.append(rival["id"])
        r = client.post(
            "/v1/auth/login",
            json={
                "identifier": rival["owner_username"],
                "password": rival["initial_password"],
            },
        )
        rival_token = r.json()["access_token"]
        client.post(
            "/v1/auth/password",
            headers=bearer(rival_token),
            json={
                "current_password": rival["initial_password"],
                "new_password": "another-good-password",
            },
        )
        r = client.post(
            "/v1/auth/login",
            json={"identifier": rival["owner_username"], "password": "another-good-password"},
        )
        rival_token = r.json()["access_token"]

        r = client.get("/v1/events", headers=bearer(rival_token))
        check(
            r.status_code == 200 and r.json()["items"] == [],
            "a second studio sees none of the first studio's events",
            r.text[:160],
        )

        r = client.get(f"/v1/events/{event_id}", headers=bearer(rival_token))
        check(
            r.status_code == 404,
            "another studio's event reads as not found, not as forbidden",
            r.text[:120],
        )

        # ── a photographer is not an owner ──────────────────────────────
        r = client.post(
            "/v1/studio/members",
            headers=bearer(owner_token),
            json={"username": f"shooter{marker}", "name": "Second Shooter", "role": "photographer"},
        )
        check(r.status_code == 201, "owner creates a photographer", r.text[:160])
        shooter = r.json()
        r = client.post(
            "/v1/auth/login",
            json={"identifier": shooter["username"], "password": shooter["initial_password"]},
        )
        shooter_token = r.json()["access_token"]
        client.post(
            "/v1/auth/password",
            headers=bearer(shooter_token),
            json={
                "current_password": shooter["initial_password"],
                "new_password": "shooter-password-1",
            },
        )
        r = client.post(
            "/v1/auth/login",
            json={"identifier": shooter["username"], "password": "shooter-password-1"},
        )
        shooter_token = r.json()["access_token"]

        r = client.post(f"/v1/events/{event_id}/checkout", headers=bearer(shooter_token))
        check(r.status_code == 403, "a photographer cannot pay for an event", r.text[:120])

        r = client.post(
            "/v1/studio/members",
            headers=bearer(shooter_token),
            json={"username": f"nope{marker}", "role": "photographer"},
        )
        check(r.status_code == 403, "a photographer cannot create accounts")

        r = client.get("/v1/admin/studios", headers=bearer(owner_token))
        check(r.status_code == 403, "a studio owner cannot reach the admin panel")

        # ── login throttling ────────────────────────────────────────────
        codes = [
            client.post(
                "/v1/auth/login", json={"identifier": owner_user, "password": "wrong-password"}
            ).status_code
            for _ in range(12)
        ]
        check(429 in codes, "repeated failed logins are throttled", f"codes={set(codes)}")

    finally:
        with system_session() as db:
            db.execute(
                text("DELETE FROM users WHERE username = :u"), {"u": admin_user}
            )
            for sid in studio_ids:
                db.execute(text("DELETE FROM studios WHERE id = :i"), {"i": sid})
            db.execute(
                text("DELETE FROM login_attempts WHERE identifier LIKE :m"), {"m": f"%{marker}%"}
            )
            # Payment ids here are made up, so unlike real ones they would
            # collide with the previous run and be dropped as duplicates.
            db.execute(
                text("DELETE FROM webhook_events WHERE external_id LIKE :m"), {"m": f"%{marker}%"}
            )
            # Storage as well. Deleting only the rows leaves the objects behind,
            # which is the same bug the retention purge exists to avoid.
            for sid in studio_ids:
                for key in db.execute(
                    text("SELECT storage_key FROM photos WHERE studio_id = :s"), {"s": sid}
                ).scalars():
                    try:
                        get_storage().delete(key)
                    except Exception:  # noqa: BLE001
                        pass
            db.commit()

    print()
    if failures:
        print(f"  {len(failures)} check(s) failed:")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  the API behaves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
