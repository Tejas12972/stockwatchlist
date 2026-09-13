"""Startup behaviour: migrations, health split, request ids.

The migration tests cover a crash this project actually had. `init_db()` runs on
every CLI command and builds the schema with `create_all`, which leaves no
`alembic_version` row. Starting the API against that database then ran
`upgrade head`, which tried to create the tables a second time and died with
"table tickers already exists" — so anyone who ran `options-tool snapshot`
before first starting the API could not start it at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from options_tool.api.main import _run_migrations
from options_tool.config import get_settings
from options_tool.db.models import Base


@pytest.fixture
def migration_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    monkeypatch.setitem(os.environ, "OPTIONS_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def tables_in(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


class TestMigrationsOnStartup:
    def test_empty_database_is_migrated_to_head(self, migration_db: str) -> None:
        _run_migrations()
        tables = tables_in(migration_db)
        assert {"tickers", "snapshots", "contracts", "alembic_version"} <= tables

    def test_an_unversioned_schema_is_adopted_not_recreated(self, migration_db: str) -> None:
        """The regression. Previously this raised "table tickers already exists"."""
        engine = create_engine(migration_db)
        Base.metadata.create_all(engine)
        engine.dispose()

        assert "alembic_version" not in tables_in(migration_db)

        _run_migrations()  # must not raise

        tables = tables_in(migration_db)
        assert "alembic_version" in tables
        assert "tickers" in tables

    def test_running_twice_is_harmless(self, migration_db: str) -> None:
        """A container restart loop re-runs this on every boot."""
        _run_migrations()
        _run_migrations()
        assert "alembic_version" in tables_in(migration_db)

    def test_creates_the_parent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh volume is mounted empty; nothing else creates /data/..."""
        nested = tmp_path / "does" / "not" / "exist" / "o.db"
        monkeypatch.setitem(os.environ, "OPTIONS_DATABASE_URL", f"sqlite:///{nested}")
        get_settings.cache_clear()
        try:
            _run_migrations()
            assert nested.exists()
        finally:
            get_settings.cache_clear()


class TestHealthEndpoints:
    def test_live_is_cheap_and_dependency_free(self, api_client: TestClient) -> None:
        """Restarting a container cannot fix a database problem, so liveness
        must not consult the database."""
        response = api_client.get("/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    def test_health_reports_readiness_details(self, api_client: TestClient) -> None:
        body = api_client.get("/health").json()
        assert body["status"] == "ok"
        assert body["database_reachable"] is True
        assert body["provider"] == "fixture"

    def test_health_reports_whether_the_scheduler_is_running(self, api_client: TestClient) -> None:
        """A deployment that silently stopped accumulating history should be
        visible here, not discovered weeks later when IV rank never arrives."""
        body = api_client.get("/health").json()
        assert body["snapshot_scheduler"] is False
        assert body["snapshot_at"] is None


class TestRequestIds:
    def test_every_response_carries_one(self, api_client: TestClient) -> None:
        response = api_client.get("/watchlist")
        assert response.headers.get("x-request-id")

    def test_a_supplied_id_is_echoed_back(self, api_client: TestClient) -> None:
        """Lets a caller correlate its own logs with this service's."""
        response = api_client.get("/watchlist", headers={"x-request-id": "abc123"})
        assert response.headers["x-request-id"] == "abc123"

    def test_ids_differ_between_requests(self, api_client: TestClient) -> None:
        first = api_client.get("/watchlist").headers["x-request-id"]
        second = api_client.get("/watchlist").headers["x-request-id"]
        assert first != second


class TestCorsIsOffByDefault:
    def test_no_cors_headers_without_configured_origins(self, api_client: TestClient) -> None:
        """The deployed front end proxies server-side, so no browser ever makes
        a cross-origin request to this service. An allow-list that is not needed
        is one more thing to get wrong."""
        response = api_client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in response.headers
