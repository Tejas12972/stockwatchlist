"""FastAPI application.

    uvicorn options_tool.api.main:app --reload

Docs are at `/docs`. Every response is a Pydantic model and every failure is a
typed error body -- see `errors.py`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from options_tool import __version__
from options_tool.api.errors import register_exception_handlers
from options_tool.api.routes import router

__all__ = ["app", "create_app"]

logger = logging.getLogger(__name__)

DESCRIPTION = """
Personal options analytics.

**Greeks and implied volatility on every response are computed by this service**,
not read from the market-data vendor. Black-Scholes-Merton for pricing, Newton-
Raphson with a bisection fallback for the implied-vol solve.

Where a value cannot be established it is returned as `null` with a status
explaining why — an unsolvable contract carries `iv: null` and an `iv_status`,
and `/ivrank` returns `rank: null` with a human-readable `reason` until enough
history has accumulated. Nothing here substitutes a plausible-looking default.

_Not investment advice. A personal analytics tool; market data is delayed and
sourced from an unofficial endpoint._
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Options Analytics API",
        version=__version__,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # The Next.js front end is served from a different origin in development.
    # Permissive here because this is a single-user tool with no authentication
    # and no secrets to leak; a multi-user deployment must narrow it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
