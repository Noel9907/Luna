"""
Checkout, the webhook, and payment history.

The one rule: THE WEBHOOK ACTIVATES AN EVENT, THE BROWSER NEVER DOES.

A studio pays on a phone at a venue with bad signal, or closes the tab the
moment the UPI app says success. If activation depended on the browser coming
back, some studios would pay and then be unable to upload at a wedding that is
already happening. The client's job after checkout is to poll the event until
the server says `active`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, razorpay
from app.audit import record
from app.config import settings
from app.db import get_system_db
from app.errors import ApiError
from app.models import Event, Payment, Studio, WebhookEvent
from app.routers.events import event_json, load_event
from app.security import Principal

router = APIRouter(tags=["billing"])


@router.post("/events/{event_id}/checkout", status_code=201)
def create_checkout(
    event_id: str,
    who: Principal = Depends(auth.principal),
    db: Session = Depends(auth.owner_db),
):
    """
    Creates, or returns, the open order for a draft event.

    Deliberately idempotent. A studio that opens the Razorpay modal, closes it,
    and taps pay again must not end up with two orders and two charges, and
    that sequence is completely ordinary on a phone.
    """
    e = load_event(db, event_id)
    if e.status != "draft":
        raise ApiError(409, "ALREADY_ACTIVE", "That event has already been paid for.")

    existing = db.scalar(
        select(Payment).where(Payment.event_id == event_id, Payment.status == "created")
    )
    if existing is not None:
        payment = existing
    else:
        try:
            order = razorpay.create_order(
                amount_paise=e.price_paise,
                receipt=e.id,
                notes={"event_id": e.id, "studio_id": e.studio_id, "tier": e.tier_code},
            )
        except razorpay.RazorpayError as exc:
            raise ApiError(502, "PAYMENT_PROVIDER_ERROR", "Could not reach Razorpay.") from exc

        payment = Payment(
            studio_id=e.studio_id,
            event_id=e.id,
            tier_code=e.tier_code,
            amount_paise=e.price_paise,
            order_id=order["id"],
            status="created",
        )
        db.add(payment)
        db.commit()

    studio = db.get(Studio, who.studio_id)
    return {
        "razorpay_order_id": payment.order_id,
        "amount_paise": payment.amount_paise,
        "currency": "INR",
        # The PUBLIC key. The secret never leaves this process and the client
        # never computes a signature.
        #
        # In fake mode this is the literal string the desktop app checks for to
        # skip loading Razorpay's real SDK. Signalling the mode through the key
        # rather than an extra field means production sends exactly what the
        # contract says and nothing has a "test mode" branch to get wrong.
        "key_id": settings().razorpay_key_id or "rzp_test_mock",
        "event_id": e.id,
        "prefill_contact": studio.phone if studio else None,
        "prefill_name": studio.name if studio else None,
    }


class VerifyIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/events/{event_id}/checkout/verify")
def verify_checkout(
    event_id: str,
    body: VerifyIn,
    db: Session = Depends(auth.owner_db),
):
    """
    Checks the signature the browser was handed back. Nothing more.

    This does NOT activate the event, and the name of the endpoint is the only
    misleading thing about it. It exists so the app can show "payment received"
    a second or two before the webhook lands, which is worth doing because the
    alternative is a spinner on a screen the studio is anxiously watching.
    """
    load_event(db, event_id)
    ok = razorpay.verify_checkout_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    )
    if not ok:
        raise ApiError(422, "BAD_SIGNATURE", "That payment could not be verified.")
    return {"verified": True}


@router.get("/payments")
def list_payments(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(auth.active_studio_db),
):
    rows = db.execute(
        select(Payment, Event)
        .join(Event, Event.id == Payment.event_id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": p.id,
                "event_id": p.event_id,
                "event_name": e.name,
                "tier_code": p.tier_code,
                "amount_paise": p.amount_paise,
                "status": p.status,
                "method": p.method,
                "razorpay_payment_id": p.payment_id,
                "created_at": p.created_at.isoformat(),
            }
            for p, e in rows
        ],
        "next_cursor": None,
    }


# ── the webhook ────────────────────────────────────────────────────────


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    db: Session = Depends(get_system_db),
):
    """
    The only thing that activates an event.

    `async def` here, unlike the selfie route, because this does no CPU work:
    it reads a small body, checks an HMAC and writes a few rows.

    Runs on the owner connection. A webhook arrives from Razorpay with no
    session and no tenant, and figuring out which studio it belongs to is the
    entire job, so it is on the short list in `app/db.py`.

    Order of operations matters and is not arbitrary:

      1. read the RAW body, before any parsing
      2. verify the signature against those exact bytes
      3. record the delivery, keyed uniquely, so a redelivery is dropped
      4. only then act on it

    Steps 3 and 4 in that order are what make redelivery safe. Razorpay retries
    for up to 24 hours and does not promise to deliver exactly once, so without
    the unique key a retry would happily activate an event a second time and
    write a second payment row.
    """
    raw = await request.body()

    if not razorpay.verify_webhook_signature(raw, x_razorpay_signature or ""):
        # 400, not 401. Razorpay retries 5xx; a signature that does not match
        # will never start matching, so asking it to try again is pointless.
        raise ApiError(400, "BAD_SIGNATURE", "Signature did not match.")

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "BAD_PAYLOAD", "Could not parse that payload.") from exc

    event_type = body.get("event", "")
    entity = body.get("payload", {}).get("payment", {}).get("entity", {})
    payment_id = entity.get("id")
    order_id = entity.get("order_id")

    if not payment_id:
        return {"ok": True, "ignored": "no payment entity"}

    # The idempotency key. Razorpay's own delivery id would be better, but it
    # is not present in every payload shape, so the payment id plus the event
    # type is used: the same payment being captured twice is the same fact.
    external_id = f"{event_type}:{payment_id}"

    already = db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == "razorpay", WebhookEvent.external_id == external_id
        )
    )
    if already is not None:
        return {"ok": True, "duplicate": True}

    delivery = WebhookEvent(
        provider="razorpay",
        external_id=external_id,
        event_type=event_type,
        payload=body,
    )
    db.add(delivery)
    db.commit()

    if event_type == "payment.captured":
        _activate(db, order_id, payment_id, entity)
    elif event_type == "payment.failed":
        _mark_failed(db, order_id, entity)

    delivery.processed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


def _activate(db: Session, order_id: str | None, payment_id: str, entity: dict) -> None:
    payment = db.scalar(select(Payment).where(Payment.order_id == order_id))
    if payment is None:
        return

    # The amount is checked against what we asked for. Without this, a caller
    # who could mint a valid signature for a one rupee order could activate a
    # six thousand rupee event.
    if int(entity.get("amount", 0)) != payment.amount_paise:
        payment.status = "failed"
        payment.failure_reason = "amount mismatch"
        db.commit()
        return

    if payment.status != "captured":
        payment.status = "captured"
        payment.payment_id = payment_id
        payment.method = entity.get("method")
        payment.paid_at = datetime.now(timezone.utc)

    event = db.get(Event, payment.event_id)
    if event is not None and event.status == "draft":
        event.status = "active"
        event.activated_at = datetime.now(timezone.utc)
        record(
            db,
            actor_user_id=None,
            actor_label="razorpay webhook",
            action="event.activated",
            target_type="event",
            target_id=event.id,
            studio_id=event.studio_id,
            detail={"payment_id": payment_id, "amount_paise": payment.amount_paise},
        )
    db.commit()


def _mark_failed(db: Session, order_id: str | None, entity: dict) -> None:
    payment = db.scalar(select(Payment).where(Payment.order_id == order_id))
    if payment is None or payment.status == "captured":
        return
    payment.status = "failed"
    payment.failure_reason = (entity.get("error_description") or "")[:200] or None
    db.commit()


# ── development only ───────────────────────────────────────────────────


@router.post("/webhooks/razorpay/simulate")
def simulate_webhook(
    event_id: str,
    method: str = "upi",
    db: Session = Depends(get_system_db),
):
    """
    Fires a correctly signed webhook at ourselves, so the real path can be
    tested with no Razorpay account.

    It builds the same payload Razorpay sends, signs it with the same secret,
    and calls the same handler. Nothing about the production path is stubbed
    out, which is the point: a test that skips the signature check would not be
    testing the thing most likely to be wrong.
    """
    if settings().env == "production":
        raise ApiError(404, "NOT_FOUND", "Not available.")

    payment = db.scalar(
        select(Payment).where(Payment.event_id == event_id, Payment.status == "created")
    )
    if payment is None:
        raise ApiError(404, "NOT_FOUND", "No open order for that event.")

    payment_id = f"pay_fake{payment.order_id[-8:]}"
    raw = razorpay.fake_webhook_payload(
        payment.order_id, payment_id, payment.amount_paise, method
    )
    body = json.loads(raw)
    entity = body["payload"]["payment"]["entity"]

    external_id = f"payment.captured:{payment_id}"
    if db.scalar(select(WebhookEvent).where(WebhookEvent.external_id == external_id)) is None:
        db.add(
            WebhookEvent(
                provider="razorpay",
                external_id=external_id,
                event_type="payment.captured",
                payload=body,
                processed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        _activate(db, payment.order_id, payment_id, entity)

    event = db.get(Event, event_id)
    return event_json(event) if event else {"ok": True}
