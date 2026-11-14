"""One error format for every endpoint.

    {"detail": "<readable message>", "code": "<stable code>", "errors": [...] | null}

* `detail` is always a string (FastAPI's own 422s used to put a list there).
* `code` is stable, for the frontend to branch on: not_found, validation_error,
  service_unavailable, internal_error, or http_<status> for anything else. An
  endpoint can be more specific by raising ApiError (e.g. job_not_found).
* `errors` lists the field problems of a 422 validation error (loc, msg, type);
  null otherwise.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    422: "validation_error",
    500: "internal_error",
    503: "service_unavailable",
}


class ApiError(StarletteHTTPException):
    """An HTTP error with a specific `code` (instead of the one derived from the status)."""

    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def error_body(status_code: int, detail: str, errors: list[dict[str, Any]] | None = None,
               code: str | None = None) -> dict[str, Any]:
    return {"detail": detail, "code": code or CODES.get(status_code, f"http_{status_code}"), "errors": errors}


def _field(loc: tuple | list) -> str:
    parts = [str(p) for p in loc if p not in ("query", "body", "path", "header")]
    return ".".join(parts) or "request"


async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(error_body(exc.status_code, detail, code=getattr(exc, "code", None)),
                        status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
              for e in exc.errors()]
    detail = "; ".join(f"{_field(e['loc'])}: {e['msg']}" for e in errors) or "Invalid request"
    return JSONResponse(jsonable_encoder(error_body(422, detail, errors)), status_code=422)


async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(error_body(500, "Internal server error"), status_code=500)


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unhandled_error)
