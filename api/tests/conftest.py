"""Shared test fixtures.

Every test in this suite runs with sockets disabled (`--disable-socket` in
`pyproject.toml`). Nothing here may reach the network: market data comes from
`FixtureProvider` replaying a real capture, and the database is SQLite on disk in
a temporary directory.

That is not just hygiene. A suite that quietly depends on Yahoo being up is a
suite that fails in CI for reasons unrelated to the code, and one that passes
locally while the analytics are broken.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from options_tool.analytics.iv_rank import IVObservation
from options_tool.config import Settings, get_settings
from options_tool.db.models import Base
from options_tool.db.session import create_db_engine, get_sessionmaker
from options_tool.providers.fixture_provider import DEFAULT_FIXTURE_PATH, FixtureProvider

# Reference market state used across the analytics tests. Round numbers on
# purpose -- when an assertion fails you can check it against a textbook.
REF_SPOT = 100.0
REF_STRIKE = 100.0
REF_YEARS = 1.0
REF_RATE = 0.05
REF_SIGMA = 0.20


@pytest.fixture(scope="session")
def fixture_path() -> Path:
    if not DEFAULT_FIXTURE_PATH.exists():
        pytest.fail(
            f"missing {DEFAULT_FIXTURE_PATH}. It is checked in deliberately; "
            "regenerate with `python -m options_tool capture-fixture AAPL SPY`."
        )
    return DEFAULT_FIXTURE_PATH


@pytest.fixture
def provider(fixture_path: Path) -> FixtureProvider:
    """Offline market data replayed from a real vendor capture."""
    return FixtureProvider(fixture_path)


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    """Settings pointed at a throwaway database, with the cache cleared.

    `get_settings` is lru_cached for the process, so without clearing it one
    test's configuration leaks into every later test.
    """
    previous = dict(os.environ)
    os.environ.update(
        {
            "OPTIONS_PROVIDER": "fixture",
            "OPTIONS_DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
            "OPTIONS_RISK_FREE_RATE": "0.04",
            "OPTIONS_DIVIDEND_YIELD": "0.0",
            "OPTIONS_MIN_HISTORY_DAYS": "20",
            "OPTIONS_IV_WINDOW_DAYS": "252",
            "OPTIONS_FETCH_RISK_FREE_RATE": "false",
            # Tests build the schema directly with `Base.metadata.create_all`,
            # so the startup migration has nothing to do and would only race it.
            "OPTIONS_RUN_MIGRATIONS_ON_STARTUP": "false",
            # Never let a test process fire live vendor requests on a timer.
            "OPTIONS_SNAPSHOT_ENABLED": "false",
        }
    )
    get_settings.cache_clear()
    yield get_settings()
    os.environ.clear()
    os.environ.update(previous)
    get_settings.cache_clear()


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings.options_database_url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    session = get_sessionmaker(engine)()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def api_client(engine: Engine, provider: FixtureProvider, settings: Settings):  # type: ignore[no-untyped-def]
    """TestClient with the app's engine and provider pointed at test doubles."""
    from fastapi.testclient import TestClient

    from options_tool.api import deps
    from options_tool.api.main import create_app

    app = create_app()
    app.dependency_overrides[deps.get_engine] = lambda: engine
    app.dependency_overrides[deps.get_market_provider] = lambda: provider
    app.dependency_overrides[deps.get_api_settings] = lambda: settings

    def _session() -> Iterator[Session]:
        db = get_sessionmaker(engine)()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[deps.get_session] = _session
    with TestClient(app) as client:
        yield client


def make_observations(values: list[float], start: date | None = None) -> list[IVObservation]:
    """Consecutive daily IV observations, oldest first."""
    start = start or date(2026, 1, 1)
    return [IVObservation(start + timedelta(days=i), v) for i, v in enumerate(values)]


def utc(year: int, month: int, day: int, hour: int = 15) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)
