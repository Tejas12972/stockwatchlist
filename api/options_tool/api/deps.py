"""Shared dependencies for the API layer."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from options_tool.config import Settings, get_settings
from options_tool.db.session import get_sessionmaker, init_db
from options_tool.providers import MarketDataProvider, get_provider

__all__ = ["get_engine", "get_session", "get_market_provider", "get_api_settings"]


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """One engine per process. `init_db` makes a clean clone serve immediately."""
    return init_db()


def get_session() -> Iterator[Session]:
    """Request-scoped session. Commits on success so POSTs persist."""
    session = get_sessionmaker(get_engine())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@lru_cache(maxsize=1)
def get_market_provider() -> MarketDataProvider:
    """The configured provider. Cached so the fixture file is read once."""
    return get_provider()


def get_api_settings() -> Settings:
    return get_settings()
