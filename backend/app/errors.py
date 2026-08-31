"""
One error shape for the whole API.

Both frontends switch on `error.code`, never on the HTTP status and never on
the message, so the code is part of the contract and the message is not. That
split is what lets the copy be rewritten without breaking a client, and it is
why every raise here names a code explicitly rather than letting FastAPI
produce its own `detail` string.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status_code=status, detail={"code": code, "message": message})


def err(status: int, code: str, message: str) -> JSONResponse:
    """For the few places that return rather than raise."""
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return err(
        exc.status_code,
        detail.get("code", "ERROR"),
        detail.get("message", str(exc.detail)),
    )


async def validation_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """
    Pydantic rejections, flattened into the same envelope.

    FastAPI's default 422 body is a nested list of validation objects, which no
    frontend here knows how to render. It becomes one code the client can
    switch on.
    """
    return err(422, "INVALID_REQUEST", "Some of those details are not valid.")


# Codes used in more than one place, so a typo is a NameError rather than a
# silently different string on the wire.
NOT_FOUND = "NOT_FOUND"
FORBIDDEN = "FORBIDDEN"
UNAUTHENTICATED = "UNAUTHENTICATED"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
EVENT_NOT_ACTIVE = "EVENT_NOT_ACTIVE"
