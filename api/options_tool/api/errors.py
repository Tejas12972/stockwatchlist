"""Typed API errors.

Every failure leaves this service as a structured body with a machine-readable
`code`, an explanation, and an HTTP status that says whose problem it is. There
is no path that produces a bare 500 with a stack trace: the catch-all handler
logs the traceback server-side and returns `internal_error` to the client.

The distinction that matters most here is 503 vs 404. Yahoo throttling us is a
transient upstream failure the caller should retry; a symbol that does not exist
is not. Collapsing both into 500 would make the front end unable to tell the
user anything useful.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from options_tool.providers.base import (
    ExpiryNotFoundError,
    ProviderError,
    RateLimitedError,
    UnknownTickerError,
)

__all__ = ["ErrorBody", "APIError", "register_exception_handlers"]

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    """The shape of every error response this API returns."""

    code: str = Field(description="Stable machine-readable identifier for the failure.")
    message: str = Field(description="Human-readable explanation, safe to show a user.")
    detail: dict[str, Any] | None = Field(
        default=None, description="Optional structured context, e.g. available expiries."
    )


class APIError(Exception):
    """Raise inside a route to return a typed error response."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail

    def response(self) -> JSONResponse:
        body = ErrorBody(code=self.code, message=self.message, detail=self.detail)
        return JSONResponse(status_code=self.status_code, content=body.model_dump())


def register_exception_handlers(app: FastAPI) -> None:
    """Map every exception this service can raise onto a typed response."""

    @app.exception_handler(APIError)
    async def _api_error(_request: Request, exc: APIError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(UnknownTickerError)
    async def _unknown_ticker(_request: Request, exc: UnknownTickerError) -> JSONResponse:
        return APIError("unknown_ticker", str(exc), status.HTTP_404_NOT_FOUND).response()

    @app.exception_handler(ExpiryNotFoundError)
    async def _expiry_not_found(_request: Request, exc: ExpiryNotFoundError) -> JSONResponse:
        # The caller asked for a date that isn't listed. Hand back the ones that
        # are, so the client can correct itself without a second round trip.
        return APIError(
            "expiry_not_found",
            str(exc),
            status.HTTP_404_NOT_FOUND,
            detail={"available_expiries": [d.isoformat() for d in exc.available]},
        ).response()

    @app.exception_handler(RateLimitedError)
    async def _rate_limited(_request: Request, exc: RateLimitedError) -> JSONResponse:
        # 503, not 500: the caller did nothing wrong and retrying may well work.
        return APIError(
            "provider_rate_limited", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE
        ).response()

    @app.exception_handler(ProviderError)
    async def _provider_error(_request: Request, exc: ProviderError) -> JSONResponse:
        return APIError("provider_error", str(exc), status.HTTP_502_BAD_GATEWAY).response()

    @app.exception_handler(ValueError)
    async def _value_error(_request: Request, exc: ValueError) -> JSONResponse:
        # Analytics raise ValueError for inputs that are wrong rather than merely
        # degenerate (a non-positive strike, a zero-quantity leg). That is a 400.
        return APIError("invalid_input", str(exc), status.HTTP_400_BAD_REQUEST).response()

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return APIError(
            "internal_error",
            "An unexpected error occurred. The details have been logged server-side.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ).response()
