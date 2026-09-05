"""
The API.

This file does assembly only: middleware, error handlers, startup checks and
router mounting. The routes live in `app/routers/`, split the way the product
is split rather than by HTTP verb, so the guest code and the studio code never
end up interleaved in one 2,000 line file.

Check what you built against what you promised, any time:

    the spec        api-contract-v1.yaml
    what exists     http://localhost:8000/openapi.json
"""

from __future__ import annotations

import pathlib
import sys

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import RLS_ACTIVE
from app.errors import http_exception_handler, validation_exception_handler
from app.routers import admin, auth_routes, billing, events, guest, misc, studio, uploads

V1 = "/v1"

app = FastAPI(
    title="Frame API",
    version="1.0.0",
    docs_url="/docs" if settings().env != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origins,
    # No cookies anywhere in this API: staff send a bearer token and guests send
    # an opaque session header, both read from local storage by code on their
    # own origin. Asking browsers to attach credentials would grant a permission
    # nothing uses, and it is the credentialed case that makes CORS subtle.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


@app.on_event("startup")
def check_configuration() -> None:
    """
    Refuses to start a production process that is configured unsafely.

    Every item on this list is something that fails silently rather than
    loudly: a default JWT secret still signs valid tokens, and an application
    connected as the database owner still answers every query, just without any
    tenant isolation at all. A server that will not boot is a far better
    outcome than one that boots and quietly has no security.
    """
    problems = settings().verify_production()
    if problems:
        print("\n  refusing to start:\n")
        for p in problems:
            print(f"    - {p}")
        print()
        sys.exit(1)

    if settings().env != "production" and not RLS_ACTIVE:
        print(
            "  note: DATABASE_APP_URL is not set, so this process is connected as the\n"
            "        database owner and row-level security is NOT in effect. Fine for\n"
            "        development. Run scripts/prove_isolation.py to test it properly."
        )


# Order matters for exactly one pair of routes: the static `/g/photos` and
# `/g/session` must be registered before the dynamic `/g/{qr_token}`, or
# "photos" is captured as a QR token and every gallery request 404s. The guest
# router registers them in that order internally.
app.include_router(misc.router, prefix=V1)
app.include_router(auth_routes.router, prefix=V1)
app.include_router(events.router, prefix=V1)
app.include_router(uploads.router, prefix=V1)
app.include_router(billing.router, prefix=V1)
app.include_router(studio.router, prefix=V1)
app.include_router(admin.router, prefix=V1)
app.include_router(guest.router, prefix=V1)


# ── the guest app, served from this same origin ────────────────────────
#
# Registered LAST, after every /v1 route, because the catch-all below would
# otherwise swallow the API.
#
# Serving the built SPA from the API turns two origins into one, and that is
# the whole point: no CORS, no second tunnel, and no VITE_API_BASE baked at
# build time to get wrong. `shared/lib/api.ts` already defaults to the relative
# '/v1', so a production build talks to whatever host served it. Build with:
#
#     npm run build --workspace web
#
# If web/dist is missing the API still runs; only the guest pages 404, which is
# the right failure when the desktop app is the only client.

_DIST = pathlib.Path(__file__).resolve().parent.parent.parent / "web" / "dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """
        Every non-API path returns index.html and the SPA routes on the URL.

        The guest app reads its token out of /g/<token> in the browser, so the
        server must hand the same document to every one of those paths rather
        than looking for a file that was never on disk.
        """
        candidate = (_DIST / full_path).resolve()
        # Only serve real files that are genuinely inside dist. Without the
        # containment check a request for ../../.env would be honoured.
        if full_path and candidate.is_file() and _DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
