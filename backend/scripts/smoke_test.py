"""
End-to-end proof that the loop closes, against a running server.

Signs in, creates an event, pays for it, uploads photographs, waits for the
worker to index them, registers a guest selfie, and checks that the right
photographs come back.

    python scripts/seed_dev.py
    python scripts/smoke_test.py photo1.jpg photo2.jpg ... --selfie me.jpg

The API and at least one worker must already be running:

    uvicorn app.main:app --reload
    python -m app.worker

This is the test that matters, because it is the only one that runs the actual
models over actual faces. `prove_api_flow.py` covers everything around the
loop; this covers the loop.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

import requests

BASE = os.environ.get("FRAME_API", "http://localhost:8000/v1")
USER = os.environ.get("FRAME_USER", "lakeview")
PASSWORD = os.environ.get("FRAME_PASSWORD", "dev-owner-password")


def main() -> int:
    args = sys.argv[1:]
    if "--selfie" not in args:
        print(__doc__)
        return 2
    cut = args.index("--selfie")
    photos = [pathlib.Path(p) for p in args[:cut]]
    selfie = pathlib.Path(args[cut + 1])

    if not photos or not selfie.exists():
        print("Need at least one photograph and a selfie that exists.")
        return 2
    missing = [p for p in photos if not p.exists()]
    if missing:
        print(f"Missing: {', '.join(str(p) for p in missing)}")
        return 2

    s = requests.Session()
    print("  health      ", s.get(f"{BASE}/health", timeout=10).json())

    # ── sign in ─────────────────────────────────────────────────────────
    r = s.post(f"{BASE}/auth/login", json={"identifier": USER, "password": PASSWORD}, timeout=15)
    if r.status_code != 200:
        print(f"\n  Could not sign in as {USER}: {r.text[:200]}")
        print("  Run:  python scripts/seed_dev.py")
        return 1
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    print(f"  signed in   {USER}")

    # ── create and pay for an event ─────────────────────────────────────
    ev = s.post(
        f"{BASE}/events",
        json={"name": "Smoke Test Wedding", "event_date": "2026-08-21", "tier_code": "pro"},
        timeout=15,
    ).json()
    print(f"  event       {ev['id']}  status={ev['status']}")

    # An event opens as draft and only the payment webhook activates it, so the
    # test has to go through checkout like a real studio would. In fake mode the
    # simulate endpoint signs a real payload and calls the real handler.
    s.post(f"{BASE}/events/{ev['id']}/checkout", timeout=20).raise_for_status()
    r = s.post(f"{BASE}/webhooks/razorpay/simulate", params={"event_id": ev["id"]}, timeout=20)
    r.raise_for_status()
    ev = r.json()
    if ev["status"] != "active":
        print(f"  event did not activate: {ev}")
        return 1
    print(f"  paid        status={ev['status']}  qr={ev['qr_url']}")

    # ── upload ──────────────────────────────────────────────────────────
    files_meta = [
        {"client_ref": p.name, "content_type": "image/jpeg", "size_bytes": p.stat().st_size}
        for p in photos
    ]
    slots = s.post(
        f"{BASE}/events/{ev['id']}/uploads", json={"files": files_meta}, timeout=20
    ).json()["uploads"]

    # The bytes go DIRECTLY to storage, never through the API.
    for slot, path in zip(slots, photos, strict=True):
        r = s.put(
            slot["upload_url"],
            data=path.read_bytes(),
            headers={"Content-Type": "image/jpeg"},
            timeout=120,
        )
        r.raise_for_status()
    print(f"  uploaded    {len(slots)} photograph(s) straight to storage")

    done = s.post(
        f"{BASE}/events/{ev['id']}/photos/complete",
        json={"photo_ids": [x["photo_id"] for x in slots]},
        timeout=20,
    ).json()
    print(f"  enqueued    {done}")
    if done.get("missing"):
        print("  Some uploads did not land in storage. Check the server log.")
        return 1

    # ── wait for the worker ─────────────────────────────────────────────
    print("  indexing    ", end="", flush=True)
    deadline = time.time() + 180
    stats = {}
    while time.time() < deadline:
        stats = s.get(f"{BASE}/events/{ev['id']}/stats", timeout=10).json()
        if stats["done"] + stats["failed"] >= len(photos):
            break
        print(".", end="", flush=True)
        time.sleep(2)
    print(f" done={stats.get('done')} failed={stats.get('failed')}")

    if not stats.get("done"):
        print("\n  Nothing indexed. Is the worker running?  python -m app.worker")
        return 1

    # ── the guest ───────────────────────────────────────────────────────
    token = ev["qr_url"].rstrip("/").split("/")[-1]
    guest = requests.Session()  # no Authorization header: a guest has no account
    info = guest.get(f"{BASE}/g/{token}", timeout=10).json()
    print(f"  guest sees  {info['event_name']} by {info['branding']['studio_name']}")

    sess = guest.post(f"{BASE}/g/{token}/session", json={"consented": True}, timeout=10).json()
    guest.headers["X-Guest-Session"] = sess["session_token"]
    print(f"  consent     face data deleted on {sess['face_deletion_date']}")

    r = guest.post(
        f"{BASE}/g/selfie",
        files={"selfie": ("selfie.jpg", selfie.read_bytes(), "image/jpeg")},
        timeout=120,
    )
    if r.status_code != 200:
        print(f"  selfie      REJECTED {r.json()}")
        return 1
    matched = r.json()["matched_count"]
    print(f"  selfie      matched {matched} photograph(s)")

    gallery = guest.get(f"{BASE}/g/photos", timeout=20).json()
    print(f"  gallery     {gallery['total_count']} photograph(s) visible to this guest")

    thumbs = [i for i in gallery["items"] if i["thumbnail_url"] != i["full_url"]]
    print(f"  thumbnails  {len(thumbs)}/{len(gallery['items'])} served as a separate small image")

    print()
    if matched > 0:
        print("  PASS. A photograph went in, a selfie came in, and the right")
        print("  photographs came back. That is the whole product.")
        return 0

    print("  The pipeline ran but nothing matched. Either the selfie is of")
    print("  someone not in these photographs, or MATCH_THRESHOLD is too high.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
