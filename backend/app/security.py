"""
Passwords and tokens. No web framework and no database, so it can be tested on
its own.

Three separate jobs live here and they are easy to confuse:

    passwords         hashed with Argon2id, slow on purpose
    access tokens     signed JWTs, not stored anywhere, short lived
    opaque tokens     refresh tokens and guest session tokens: random strings
                      stored only as a SHA-256 digest
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings

# Argon2id at the library defaults: 64MB of memory and three passes per hash.
#
# The memory cost is the point. Bcrypt is cheap to attack with a GPU because it
# needs almost no memory, so an attacker fits thousands of parallel guesses on
# one card. Forcing 64MB per guess makes that parallelism expensive in the one
# resource graphics cards are short of.
#
# The cost is paid on login, which happens a handful of times per event, and
# never on the hot path.
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        _hasher.verify(stored_hash, plain)
        return True
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """
    True when the hash was made with weaker parameters than the current ones.

    Raising the cost later only protects people who log in afterwards unless
    something re-hashes existing passwords, and login is the one moment the
    plaintext is legitimately in hand.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True


# ── opaque tokens ──────────────────────────────────────────────────────


def new_opaque_token() -> str:
    """256 bits from the OS. Not guessable, not derived from anything."""
    return secrets.token_urlsafe(32)


def fingerprint(raw_token: str) -> str:
    """
    The digest stored in place of the token itself.

    Plain SHA-256 rather than Argon2, deliberately. A password is short and
    human-chosen, so it must be slow to guess. These tokens are 256 random
    bits, so there is nothing to guess and the only job is to make a database
    leak useless. Hashing them slowly would just make every refresh and every
    gallery poll slower for no gain.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    """Constant time, so comparison timing cannot leak a prefix."""
    return secrets.compare_digest(a, b)


# ── access tokens ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Principal:
    """
    Who is calling, taken entirely from the signed token.

    Nothing here is read from the database on the hot path. That is a real
    trade and worth stating plainly: an account disabled right now keeps
    working until its access token expires, which is at most
    ACCESS_TOKEN_MINUTES. Fifteen minutes of residual access for a photographer
    who was just removed is acceptable; a database round trip on every single
    request to shave it is not, and the refresh token is revoked immediately
    either way, so the window cannot extend past one expiry.
    """

    user_id: str
    studio_id: str | None
    role: str
    username: str
    must_change_password: bool

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"


def make_access_token(
    user_id: str,
    studio_id: str | None,
    role: str,
    username: str,
    must_change_password: bool,
) -> tuple[str, int]:
    """Returns (token, seconds_until_expiry)."""
    s = settings()
    ttl = s.access_token_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "sid": studio_id,
        "role": role,
        "un": username,
        "mcp": must_change_password,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), ttl


def read_access_token(token: str) -> Principal | None:
    """Returns None for anything not a currently valid access token."""
    s = settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],  # a list, so "alg": "none" cannot be smuggled in
        )
    except jwt.PyJWTError:
        return None

    # A refresh token must never be accepted where an access token is expected.
    if payload.get("typ") != "access":
        return None

    return Principal(
        user_id=payload["sub"],
        studio_id=payload.get("sid"),
        role=payload.get("role", "photographer"),
        username=payload.get("un", ""),
        must_change_password=bool(payload.get("mcp", False)),
    )


def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings().refresh_token_days)
