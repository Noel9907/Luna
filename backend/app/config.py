"""
Every knob in one place, read from .env.

Anything with a real default is safe in development. Anything that defaults to
empty MUST be set before production, and `verify_production()` is what stops
the server booting without them rather than discovering it during a wedding.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # "development" | "production"

    # Two connections to the same database, as two different Postgres roles.
    #
    # `database_url` is the owner. It runs migrations and the handful of
    # operations that have no tenant yet: authenticating a login, resolving a
    # QR token, claiming a job, running the retention purge, and serving the
    # platform admin panel. It bypasses row-level security because it owns the
    # tables.
    #
    # `database_app_url` is a role with no ownership and no BYPASSRLS. Every
    # ordinary studio and guest request uses it, with `app.studio_id` set for
    # the transaction. A bug in a WHERE clause on this connection returns no
    # rows rather than another studio's wedding.
    database_url: str = "postgresql+psycopg://frame:frame@localhost:55432/frame"
    database_app_url: str = ""
    # Only used when the migration has to create the restricted role.
    app_db_password: str = "frame_app"

    # ── storage ───────────────────────────────────────────────────────
    # Week 1 runs on local disk so nothing here needs a cloud account.
    # Flip to "r2" and fill the four R2 values; no other code changes.
    storage_backend: str = "local"
    local_storage_dir: str = "./storage"
    public_base_url: str = "http://localhost:8000"

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # ── where the apps live ───────────────────────────────────────────
    # The QR code encodes a guest URL, so this has to be right in production
    # or every printed QR at the venue points at localhost.
    guest_base_url: str = "*"

    # `null` is not a mistake. The packaged desktop app loads its window from
    # file://, and Chromium sends the literal string "null" as the Origin for
    # any request from a file:// page. Without it every studio on a released
    # build is blocked by CORS while development, which runs on a dev server at
    # localhost:5174, works perfectly.
    #
    # Allowing it costs nothing here. This API authenticates with a bearer
    # token in a header, never a cookie, so a hostile page in someone's browser
    # gains nothing by being allowed to send a request it has no token for.
    # CORS is not the security boundary; the token is.
    cors_origins_raw: str = "*"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    # ── matching ──────────────────────────────────────────────────────
    # Cosine similarity at or above this counts as the same person.
    #
    # Tune for PRECISION, not recall. A guest who misses a photograph is
    # disappointed; a guest who sees a stranger's photographs is a privacy
    # incident at somebody's wedding. Better to show fewer and be certain.
    #
    # 0.363 is OpenCV's SFace example value, tuned for 1:1 verification on LFW
    # pairs. This is 1:N search over ~14,000 faces per event, so a per-pair
    # false match rate that is fine for one comparison happens thousands of
    # times a night. Placeholder until scripts/tune_threshold.py replaces it
    # with a number measured on real venue photographs.
    match_threshold: float = 0.363

    # The recogniser's own input is 112x112. Anything smaller is upscaled before
    # it is embedded, so the detail the model sees is invented.
    min_face_px: int = 112
    min_detect_score: float = 0.7

    # Blur floor for indexed faces, measured on the aligned 112x112 crop. The
    # selfie gate is 40 and separate: a selfie is a close-up, a guest at the far
    # end of a hall is not. 0.0 disables it. Raise with evidence from
    # tune_threshold.py, which sweeps this and prints what it costs in recall.
    index_min_blur: float = 0.0

    # sface (128-dim, ships) or auraface (512-dim, needs onnxruntime and a
    # migration). Changing this invalidates every stored embedding.
    face_backend: str = "sface"

    # ── worker ────────────────────────────────────────────────────────
    worker_poll_seconds: float = 1.0
    max_attempts: int = 5
    # A job whose worker died becomes claimable again after this long. Short
    # enough that a crash costs minutes, long enough that a genuinely slow
    # photograph is not stolen out from under a working process.
    job_stale_minutes: int = 5

    # ── watermark ─────────────────────────────────────────────────────
    # Empty disables it entirely. When set, the text is burned into the
    # thumbnail and into a separate full-size display copy at index time. The
    # uploaded original is never modified, so turning this off restores clean
    # images without re-uploading anything.
    #
    # Applied at INDEX time, so it only affects photographs indexed after it is
    # set. Turning it on for an event already indexed means re-queueing those
    # photographs, or the full view will 404 for them.
    # A PNG with transparency, relative to backend/. Takes precedence over
    # watermark_text when both are set. Empty falls back to the text mark.
    watermark_logo: str = ""
    watermark_text: str = ""
    watermark_opacity: float = 0.75
    # Logo width as a fraction of the photograph's width, and how far its
    # baseline sits above the bottom edge as a fraction of height.
    watermark_scale: float = 0.20
    watermark_margin: float = 0.035

    # Thumbnails are generated at index time, not on request. The guest gallery
    # is the one screen every guest sees, usually on venue wifi.
    thumb_long_edge: int = 640
    thumb_quality: int = 72

    # ── auth ──────────────────────────────────────────────────────────
    # Access tokens are short because they cannot be revoked. Refresh tokens
    # are long but single-use, so a stolen one is detectable (see auth.py).
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # A photographer at a venue retries a bad password more than you expect.
    # This exists to stop credential stuffing, not to punish fat fingers.
    login_max_attempts: int = 10
    login_window_minutes: int = 15

    # ── retention ─────────────────────────────────────────────────────
    # Face retention is negotiable per studio, but never above this. Changing
    # the ceiling means editing .env and restarting, which is the right amount
    # of friction for a limit on how long biometric data is kept.
    default_face_retention_days: int = 30
    max_face_retention_days: int = 180

    purge_interval_minutes: int = 60

    # ── billing ───────────────────────────────────────────────────────
    # With no keys set, billing runs in fake mode: orders are minted locally and
    # a test endpoint fires the webhook. The real flow is exercised either way.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    @property
    def razorpay_live(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    # ── guest abuse limits ────────────────────────────────────────────
    # A selfie costs ~350ms of CPU. Without a cap, one person on the venue wifi
    # can take the whole event's processing capacity with a loop.
    selfie_max_attempts: int = 8
    guest_sessions_per_event: int = 2000

    def verify_production(self) -> list[str]:
        """Returns the reasons this config must not be used in production."""
        problems: list[str] = []
        if self.env != "production":
            return problems
        if self.jwt_secret == "dev-only-change-me" or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to at least 32 random characters")
        if self.storage_backend != "r2":
            problems.append("STORAGE_BACKEND must be r2 in production")
        if self.storage_backend == "r2" and not self.r2_bucket:
            problems.append("R2 credentials are incomplete")
        if self.public_base_url.startswith("http://"):
            problems.append("PUBLIC_BASE_URL must be https")
        if self.guest_base_url.startswith("http://"):
            problems.append("GUEST_BASE_URL must be https")
        if not self.razorpay_webhook_secret:
            problems.append("RAZORPAY_WEBHOOK_SECRET must be set or webhooks cannot be trusted")
        if not self.database_app_url:
            problems.append(
                "DATABASE_APP_URL must point at the restricted role, "
                "or every request runs with row-level security bypassed"
            )
        return problems


@lru_cache
def settings() -> Settings:
    return Settings()
