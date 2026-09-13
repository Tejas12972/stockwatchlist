"""Engine and session construction.

SQLite needs two things switched on explicitly that most people assume are
already true:

- ``PRAGMA foreign_keys=ON`` -- off by default, so ``ON DELETE CASCADE`` in the
  schema is silently ignored without it
- WAL journalling -- so the daily snapshot job writing does not block the API
  reading, which is the exact concurrency this project has
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from options_tool.config import get_settings
from options_tool.db.models import Base

__all__ = ["create_db_engine", "get_sessionmaker", "session_scope", "init_db"]


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a SQLite file so a clean clone just works."""
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url == f"{prefix}:memory:":
        return
    path = Path(url[len(prefix) :])
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Build an engine with the SQLite pragmas this project depends on."""
    url = url or get_settings().options_database_url
    _ensure_sqlite_directory(url)
    engine = create_engine(url, echo=echo, future=True)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection, _record):  # type: ignore[no-untyped-def]
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # In-memory databases have no journal to write ahead of.
            if not url.endswith(":memory:"):
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or create_db_engine(), expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception.

    A half-written snapshot is worse than no snapshot -- it would look like a
    complete day to IV rank while missing most of its chain.
    """
    session = get_sessionmaker(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> Engine:
    """Create any missing tables. Safe to call repeatedly.

    Alembic owns schema *changes*; this is the first-run convenience so a clean
    clone can snapshot immediately without a migrate step.
    """
    engine = engine or create_db_engine()
    Base.metadata.create_all(engine)
    return engine
