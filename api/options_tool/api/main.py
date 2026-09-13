"""FastAPI application.

    uvicorn options_tool.api.main:app

Docs are at `/docs`. Every response is a Pydantic model and every failure is a
typed error body -- see `errors.py`.

**This service is not intended to be exposed to the internet directly.** In the
deployed arrangement it has no public address at all: the Next.js front end
reaches it over a private network and is itself the authenticated surface. That
is why there is no authentication here -- adding a second, weaker one would
invite exposing this port on the assumption it was protected.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from options_tool import __version__
from options_tool.api.errors import register_exception_handlers
from options_tool.api.routes import router
from options_tool.config import get_settings
from options_tool.logging_config import configure_logging, request_id_var

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


def _run_migrations() -> None:
    """Bring the database schema up to head.

    Run in-process at startup rather than as a separate deploy step because this
    app is a single machine with a single attached volume — there is no window
    in which a migration container could hold the disk instead. Alembic is
    idempotent, so a restart loop re-runs this harmlessly.
    """
    from pathlib import Path  # noqa: PLC0415

    from alembic.config import Config  # noqa: PLC0415
    from sqlalchemy import inspect  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from options_tool.db.session import create_db_engine  # noqa: PLC0415

    settings = get_settings()
    root = Path(__file__).resolve().parent.parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.options_database_url)

    # Also ensures the SQLite file's parent directory exists before Alembic
    # opens it.
    engine = create_db_engine(settings.options_database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    logger.info("database has %d existing table(s)", len(tables))

    # A database created by `init_db()` -- which the CLI calls on every command
    # -- has the tables but no alembic_version row. `upgrade head` would then
    # try to create them a second time and die with "table tickers already
    # exists", so anyone who ran `options-tool snapshot` before first starting
    # the API would be unable to start it at all. Adopt the existing schema by
    # stamping it instead.
    if "alembic_version" not in tables and "tickers" in tables:
        logger.info("adopting an existing un-versioned schema; stamping at head")
        command.stamp(config, "head")
        return

    command.upgrade(config, "head")
    logger.info("database schema is at head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.options_log_format)

    logger.info(
        "starting options-tool %s (provider=%s, snapshot=%s)",
        __version__,
        settings.options_provider,
        "on" if settings.options_snapshot_enabled else "off",
    )

    if settings.options_run_migrations_on_startup:
        _run_migrations()

    scheduler = None
    if settings.options_snapshot_enabled:
        from options_tool.scheduler import SnapshotScheduler  # noqa: PLC0415

        scheduler = SnapshotScheduler(settings)
        scheduler.start()

    app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Options Analytics API",
        version=__version__,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Normally empty. The deployed front end proxies to this service server-side
    # from the same origin, so no browser ever issues a cross-origin request to
    # it — and an allow-list that is not needed is one more thing to get wrong.
    # Settable for anyone running the web app against a remote API directly.
    if settings.options_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.options_cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Tag every request with an id and log how long it took.

        A single chain request fans out into several vendor calls and retries;
        without a correlating id those log lines interleave with every other
        request and a throttling incident cannot be reconstructed afterwards.
        """
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        # Health checks fire every 30s forever; logging them buries everything else.
        if not request.url.path.startswith(("/health", "/live")):
            logger.info(
                "%s %s -> %s in %.0fms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response

    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
