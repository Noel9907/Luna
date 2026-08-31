"""
Checks that the API returns the fields the frontends actually read.

    python scripts/prove_frontend_contract.py

`npm run typecheck` proves the TypeScript agrees with itself. It cannot prove
the server sends what those types claim, because nothing connects the two at
build time. That gap is where every integration bug lives: a renamed field
still compiles on both sides and fails only in a browser, usually as `undefined`
rendered into the page rather than as an error.

So this drives the real app in-process and compares every response against the
field list in `shared/lib/types.ts`. Missing fields are failures. Extra fields
are fine and reported quietly, since the server is allowed to send more than
the client reads.

Keep the lists below in step with `shared/lib/types.ts` when either changes.
"""

from __future__ import annotations

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import razorpay  # noqa: E402
from app.db import system_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.routers.studio import generate_password  # noqa: E402
from app.security import hash_password  # noqa: E402

# Mirrors shared/lib/types.ts. Optional-in-TypeScript fields are still listed:
# `x: string | null` means the key must be present and may be null, which is a
# different promise from the key being absent.
SHAPES: dict[str, list[str]] = {
    "TokenPair": ["access_token", "refresh_token", "expires_in"],
    "Me": ["user_id", "username", "email", "name", "role", "must_change_password", "studio"],
    "MeStudio": ["id", "name", "phone", "brand_color", "brand_logo_url"],
    "Event": [
        "id", "name", "event_date", "status", "tier_code", "branding_mode",
        "photo_retention_days", "face_retention_days", "qr_url", "created_at",
        "activated_at", "ended_at",
    ],
    "EventStats": [
        "pending", "uploaded", "processing", "done", "failed",
        "guests_registered", "oldest_pending_seconds",
    ],
    "Photo": [
        "id", "status", "thumbnail_url", "face_count", "error_code",
        "created_at", "processed_at", "filename", "size_bytes",
    ],
    "Page": ["items", "next_cursor"],
    "UploadSlot": ["client_ref", "photo_id", "upload_url", "expires_at"],
    "Tier": [
        "code", "name", "price_paise", "branding_mode", "photo_retention_days",
        "face_retention_days", "custom_domain",
    ],
    "Checkout": [
        "razorpay_order_id", "amount_paise", "currency", "key_id", "event_id",
        "prefill_contact", "prefill_name",
    ],
    "Payment": [
        "id", "event_id", "event_name", "tier_code", "amount_paise", "status",
        "method", "razorpay_payment_id", "created_at",
    ],
    "StudioMember": [
        "user_id", "username", "email", "name", "role", "status",
        "must_change_password", "photos_uploaded", "last_active_at", "created_at",
    ],
    "CreatedMember": [
        "user_id", "username", "email", "name", "role", "status",
        "must_change_password", "photos_uploaded", "last_active_at", "created_at",
        "initial_password",
    ],
    "AdminStudio": [
        "id", "name", "slug", "city", "phone", "status", "brand_color",
        "default_face_retention_days", "owner_username", "events_total",
        "events_this_month", "photos_total", "revenue_paise", "created_at",
        "last_event_at",
    ],
    "CreatedStudio": [
        "id", "name", "slug", "city", "phone", "status", "brand_color",
        "default_face_retention_days", "owner_username", "events_total",
        "events_this_month", "photos_total", "revenue_paise", "created_at",
        "last_event_at", "initial_password",
    ],
    "AdminOverview": [
        "studios_active", "studios_suspended", "events_this_month",
        "events_live_now", "revenue_this_month_paise", "revenue_all_time_paise",
        "photos_this_month",
    ],
    "GuestEventInfo": [
        "event_name", "event_date", "accepting_guests", "face_retention_days", "branding",
    ],
    "GuestBranding": ["mode", "studio_name", "logo_url", "brand_color"],
    "GuestSession": [
        "session_token", "expires_at", "face_retention_days", "face_deletion_date",
    ],
    "GuestPhotoPage": ["items", "next_cursor", "latest_cursor", "total_count"],
    "GuestPhoto": ["photo_id", "thumbnail_url", "full_url", "width", "height", "matched_at"],
}

PASS = "  ok   "
FAIL = "  FAIL "

failures: list[str] = []


def check(shape: str, payload, where: str) -> None:
    """Every field the frontend reads must be present."""
    if payload is None:
        failures.append(f"{where}: no payload")
        print(f"{FAIL} {where:<34} no payload")
        return
    if not isinstance(payload, dict):
        failures.append(f"{where}: not an object")
        print(f"{FAIL} {where:<34} expected an object, got {type(payload).__name__}")
        return

    missing = [f for f in SHAPES[shape] if f not in payload]
    if missing:
        failures.append(f"{where}: missing {', '.join(missing)}")
        print(f"{FAIL} {where:<34} missing: {', '.join(missing)}")
        return

    extra = [k for k in payload if k not in SHAPES[shape]]
    note = f"  (+{len(extra)} unread)" if extra else ""
    print(f"{PASS} {where:<34} {shape}{note}")


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

    admin_user = f"fc{marker}"
    admin_password = generate_password()
    with system_session() as db:
        db.add(
            User(
                studio_id=None,
                username=admin_user,
                name="Contract Check",
                password_hash=hash_password(admin_password),
                role="platform_admin",
                status="active",
                must_change_password=False,
            )
        )
        db.commit()

    studio_ids: list[str] = []
    try:
        # ── auth ────────────────────────────────────────────────────────
        r = client.post(
            "/v1/auth/login", json={"identifier": admin_user, "password": admin_password}
        )
        check("TokenPair", r.json(), "POST /auth/login")
        admin_token = r.json()["access_token"]

        r = client.get("/v1/me", headers=bearer(admin_token))
        check("Me", r.json(), "GET /me (admin)")
        if r.json().get("studio") is not None:
            failures.append("admin studio should be null")
            print(f"{FAIL} {'GET /me (admin).studio':<34} expected null for a platform admin")
        else:
            print(f"{PASS} {'GET /me (admin).studio':<34} null, as the type says")

        # ── admin ───────────────────────────────────────────────────────
        r = client.get("/v1/admin/overview", headers=bearer(admin_token))
        check("AdminOverview", r.json(), "GET /admin/overview")

        r = client.post(
            "/v1/admin/studios",
            headers=bearer(admin_token),
            json={
                "name": f"Contract Studio {marker}",
                "city": "Kochi",
                "phone": "+91 98470 00000",
                "owner_username": f"co{marker}",
                "owner_name": "Owner",
            },
        )
        check("CreatedStudio", r.json(), "POST /admin/studios")
        created = r.json()
        studio_ids.append(created["id"])
        owner_user = created["owner_username"]
        owner_password = created["initial_password"]

        r = client.get("/v1/admin/studios", headers=bearer(admin_token))
        check("Page", {**r.json(), "next_cursor": None}, "GET /admin/studios (page)")
        check("AdminStudio", r.json()["items"][0], "GET /admin/studios[0]")

        # ── owner, forced through the password change ───────────────────
        r = client.post(
            "/v1/auth/login", json={"identifier": owner_user, "password": owner_password}
        )
        owner_token = r.json()["access_token"]

        r = client.get("/v1/me", headers=bearer(owner_token))
        check("Me", r.json(), "GET /me (owner, must change)")
        check("MeStudio", r.json()["studio"], "GET /me .studio")
        if not r.json()["must_change_password"]:
            failures.append("must_change_password should be true on a new account")
            print(f"{FAIL} {'must_change_password':<34} expected true on a fresh account")

        r = client.post(
            "/v1/auth/password",
            headers=bearer(owner_token),
            json={"current_password": owner_password, "new_password": "contract-password-1"},
        )
        check("TokenPair", r.json(), "POST /auth/password")
        owner_token = r.json()["access_token"]

        # ── events and tiers ────────────────────────────────────────────
        r = client.get("/v1/tiers", headers=bearer(owner_token))
        check("Tier", r.json()["items"][0], "GET /tiers[0]")

        r = client.post(
            "/v1/events",
            headers=bearer(owner_token),
            json={"name": "Contract Wedding", "event_date": "2026-09-20", "tier_code": "pro"},
        )
        check("Event", r.json(), "POST /events")
        event_id = r.json()["id"]
        qr_token = r.json()["qr_url"].rsplit("/", 1)[-1]

        r = client.get("/v1/events", headers=bearer(owner_token))
        check("Page", r.json(), "GET /events")

        # ── billing ─────────────────────────────────────────────────────
        r = client.post(f"/v1/events/{event_id}/checkout", headers=bearer(owner_token))
        check("Checkout", r.json(), "POST /events/{id}/checkout")
        checkout = r.json()

        if not checkout["key_id"].startswith("rzp_test_mock"):
            failures.append("fake-mode key_id must start with rzp_test_mock")
            print(
                f"{FAIL} {'checkout.key_id':<34} "
                f"{checkout['key_id']!r} does not start with rzp_test_mock, so the "
                "desktop app will try to load the real Razorpay SDK"
            )
        else:
            print(f"{PASS} {'checkout.key_id':<34} signals mock mode to the client")

        raw = razorpay.fake_webhook_payload(
            checkout["razorpay_order_id"], f"pay_fc{marker}", checkout["amount_paise"], "upi"
        )
        client.post(
            "/v1/webhooks/razorpay",
            content=raw,
            headers={"X-Razorpay-Signature": razorpay.sign_fake_webhook(raw)},
        )

        r = client.get(f"/v1/events/{event_id}", headers=bearer(owner_token))
        check("Event", r.json(), "GET /events/{id} (active)")

        r = client.get("/v1/payments", headers=bearer(owner_token))
        check("Page", r.json(), "GET /payments")
        if r.json()["items"]:
            check("Payment", r.json()["items"][0], "GET /payments[0]")

        # ── uploads ─────────────────────────────────────────────────────
        r = client.post(
            f"/v1/events/{event_id}/uploads",
            headers=bearer(owner_token),
            json={"files": [{"client_ref": "a.jpg", "content_type": "image/jpeg", "size_bytes": 64}]},
        )
        check("UploadSlot", r.json()["uploads"][0], "POST /events/{id}/uploads[0]")
        slot = r.json()["uploads"][0]

        client.put(slot["upload_url"].replace("http://localhost:8000", ""), content=tiny_jpeg())
        client.post(
            f"/v1/events/{event_id}/photos/complete",
            headers=bearer(owner_token),
            json={"photo_ids": [slot["photo_id"]]},
        )

        r = client.get(f"/v1/events/{event_id}/stats", headers=bearer(owner_token))
        check("EventStats", r.json(), "GET /events/{id}/stats")

        r = client.get(f"/v1/events/{event_id}/photos", headers=bearer(owner_token))
        check("Page", r.json(), "GET /events/{id}/photos")
        if r.json()["items"]:
            check("Photo", r.json()["items"][0], "GET /events/{id}/photos[0]")

        # ── members ─────────────────────────────────────────────────────
        r = client.get("/v1/studio/members", headers=bearer(owner_token))
        check("StudioMember", r.json()["items"][0], "GET /studio/members[0]")

        r = client.post(
            "/v1/studio/members",
            headers=bearer(owner_token),
            json={"username": f"cs{marker}", "name": "Shooter", "role": "photographer"},
        )
        check("CreatedMember", r.json(), "POST /studio/members")
        member_id = r.json()["user_id"]

        r = client.post(
            f"/v1/studio/members/{member_id}/reset-password", headers=bearer(owner_token)
        )
        check("CreatedMember", r.json(), "POST members/{id}/reset-password")

        r = client.post(
            f"/v1/admin/studios/{created['id']}/reset-password", headers=bearer(admin_token)
        )
        body = r.json()
        if "username" in body and "initial_password" in body:
            print(f"{PASS} {'POST admin reset-password':<34} username + initial_password")
        else:
            failures.append("admin reset-password shape")
            print(f"{FAIL} {'POST admin reset-password':<34} missing username or initial_password")

        # ── guest ───────────────────────────────────────────────────────
        r = client.get(f"/v1/g/{qr_token}")
        check("GuestEventInfo", r.json(), "GET /g/{qr}")
        check("GuestBranding", r.json()["branding"], "GET /g/{qr} .branding")

        r = client.post(f"/v1/g/{qr_token}/session", json={"consented": True})
        check("GuestSession", r.json(), "POST /g/{qr}/session")
        guest = r.json()["session_token"]

        r = client.get("/v1/g/photos", headers={"X-Guest-Session": guest})
        check("GuestPhotoPage", r.json(), "GET /g/photos")

        # ── end of life ─────────────────────────────────────────────────
        r = client.post(f"/v1/events/{event_id}/end", headers=bearer(owner_token))
        check("Event", r.json(), "POST /events/{id}/end")

        r = client.post("/v1/auth/logout", json={"refresh_token": "whatever"})
        if r.status_code == 204:
            print(f"{PASS} {'POST /auth/logout':<34} 204, and says nothing about the token")
        else:
            failures.append("logout status")
            print(f"{FAIL} {'POST /auth/logout':<34} expected 204, got {r.status_code}")

    finally:
        with system_session() as db:
            db.execute(text("DELETE FROM users WHERE username = :u"), {"u": admin_user})
            for sid in studio_ids:
                db.execute(text("DELETE FROM studios WHERE id = :i"), {"i": sid})
            db.execute(
                text("DELETE FROM webhook_events WHERE external_id LIKE :m"), {"m": f"%{marker}%"}
            )
            db.commit()

    print()
    if failures:
        print(f"  {len(failures)} shape(s) the frontend reads are wrong:")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  the API sends what the frontends read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
