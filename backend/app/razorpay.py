"""
Razorpay, over plain HTTP.

No SDK on purpose. The whole integration is one POST to create an order and
two HMAC comparisons, and writing those out means every security-relevant line
is visible here rather than three layers down in somebody else's package.

Two different signatures appear below and they are not interchangeable:

    checkout signature   HMAC of "order_id|payment_id" with the API SECRET.
                         Proves the browser's handback was not tampered with.
                         Cosmetic: it only lets the app show "payment received"
                         a second earlier.

    webhook signature    HMAC of the RAW REQUEST BODY with the WEBHOOK SECRET.
                         This is the one that money depends on, because the
                         webhook is what activates an event.

With no keys configured this runs in fake mode, so the entire flow including
the webhook can be exercised locally before a Razorpay account exists. Fake
mode refuses to start if ENV is production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

import requests

from app.config import settings

API = "https://api.razorpay.com/v1"
TIMEOUT = 15

# Used only when no real credentials are configured, so that signatures still
# have to be correct while developing. It is not a secret and never protects
# anything: fake mode is refused in production.
FAKE_SECRET = "fake-mode-not-a-secret"


class RazorpayError(RuntimeError):
    pass


def _secret() -> str:
    return settings().razorpay_key_secret or FAKE_SECRET


def create_order(amount_paise: int, receipt: str, notes: dict[str, str]) -> dict:
    """
    Creates an order and returns Razorpay's JSON.

    `receipt` is our own event id. When a webhook arrives months later and
    something does not line up, that field is how a payment is traced back to
    an event without trusting anything the client sent.
    """
    s = settings()
    if not s.razorpay_live:
        return {
            "id": f"order_fake{secrets.token_hex(8)}",
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "status": "created",
            "notes": notes,
        }

    response = requests.post(
        f"{API}/orders",
        auth=(s.razorpay_key_id, s.razorpay_key_secret),
        json={
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": notes,
            # Razorpay will not create a second order with the same receipt,
            # which is a free guard against a double click creating two orders.
            "payment_capture": 1,
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise RazorpayError(f"razorpay order failed: {response.status_code} {response.text[:300]}")
    return response.json()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Checks what Razorpay Checkout handed back to the browser.

    Worth being clear about what this is and is not. It proves the browser's
    message was not altered. It does NOT prove the money arrived, and it must
    never activate an event on its own: a browser can simply never come back.
    """
    expected = hmac.new(
        _secret().encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """
    The one that matters.

    Computed over the RAW bytes of the request, before any JSON parsing.
    Re-serialising the parsed body and hashing that is the classic way to break
    this: key order and whitespace change, the digest changes, and every real
    webhook starts failing while a crafted one still validates if the attacker
    matches your serialiser.
    """
    s = settings()
    secret = s.razorpay_webhook_secret or (FAKE_SECRET if not s.razorpay_live else "")
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def fake_webhook_payload(order_id: str, payment_id: str, amount_paise: int, method: str) -> bytes:
    """Builds a payload shaped like Razorpay's, for local testing."""
    return json.dumps(
        {
            "entity": "event",
            "event": "payment.captured",
            "created_at": int(time.time()),
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount_paise,
                        "currency": "INR",
                        "status": "captured",
                        "method": method,
                    }
                }
            },
        }
    ).encode()


def sign_fake_webhook(raw_body: bytes) -> str:
    s = settings()
    secret = s.razorpay_webhook_secret or FAKE_SECRET
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
