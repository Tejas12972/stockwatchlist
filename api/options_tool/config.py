"""Runtime settings, read from the environment (and `.env` when present).

Every pricing assumption lives here rather than being buried as a literal in the
maths, because the README has to state them honestly and a number you can't find
is a number you can't document. See README "Limitations".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]

# Repo-root-relative default so a clean clone works with no configuration.
_API_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuration for analytics, storage and the market-data provider."""

    model_config = SettingsConfigDict(
        env_file=(".env", str(_API_DIR / ".env"), str(_API_DIR.parent / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Market data ---------------------------------------------------------
    options_provider: str = Field(
        default="yfinance",
        description="Default market-data provider: 'yfinance' or 'fixture'.",
    )
    polygon_api_key: str | None = None
    tradier_api_key: str | None = None

    # --- Pricing assumptions -------------------------------------------------
    options_risk_free_rate: float = Field(
        default=0.04,
        ge=-0.05,
        le=0.50,
        description="Annualised continuously-compounded risk-free rate.",
    )
    options_fetch_risk_free_rate: bool = Field(
        default=False,
        description="Fetch the 13-week T-bill (^IRX) instead of using the constant.",
    )
    options_dividend_yield: float = Field(
        default=0.0,
        ge=0.0,
        le=0.50,
        description="Continuous dividend yield assumed for every underlying.",
    )

    # --- IV rank -------------------------------------------------------------
    options_iv_window_days: int = Field(
        default=252,
        ge=2,
        description="Trailing window for IV rank and percentile, in stored days.",
    )
    options_min_history_days: int = Field(
        default=20,
        ge=1,
        description=(
            "Stored days required before an IV rank is reported at all. Below "
            "this the API returns null with a reason -- never a fabricated zero."
        ),
    )

    # --- Storage -------------------------------------------------------------
    options_database_url: str = Field(default="sqlite:///./data/options.db")

    # --- Daily snapshot (deployed runs) --------------------------------------
    options_snapshot_enabled: bool = Field(
        default=False,
        description=(
            "Run the daily snapshot inside the API process. Off by default so "
            "local development and tests never fire live vendor requests."
        ),
    )
    options_snapshot_at: str = Field(
        default="21:15",
        description=(
            "24h UTC time for the daily snapshot. 21:15 UTC is after the US "
            "equity close in both EST and EDT."
        ),
    )

    # --- Operations ----------------------------------------------------------
    options_cors_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Browser origins allowed to call this API directly. Empty in the "
            "deployed arrangement, where the front end proxies server-side and "
            "no cross-origin request is ever made."
        ),
    )
    options_log_format: str = Field(
        default="text", description="'text' for local readability, 'json' for a log pipeline."
    )
    options_run_migrations_on_startup: bool = Field(
        default=True,
        description="Apply Alembic migrations when the API starts. Safe and idempotent.",
    )

    @field_validator("options_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in {"yfinance", "fixture"}:
            raise ValueError(f"unknown provider {value!r}; expected 'yfinance' or 'fixture'")
        return normalised

    @field_validator("options_snapshot_at")
    @classmethod
    def _valid_schedule_time(cls, value: str) -> str:
        # Validated here rather than at first fire: a typo should stop the
        # process at startup, not silently skip the snapshot for weeks.
        # Imported inside the validator because scheduler imports config.
        from options_tool.scheduler import parse_schedule_time  # noqa: PLC0415

        parse_schedule_time(value)
        return value.strip()

    @field_validator("options_log_format")
    @classmethod
    def _known_log_format(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in {"text", "json"}:
            raise ValueError(f"unknown log format {value!r}; expected 'text' or 'json'")
        return normalised

    @field_validator("options_min_history_days")
    @classmethod
    def _history_fits_window(cls, value: int, info) -> int:  # type: ignore[no-untyped-def]
        window = info.data.get("options_iv_window_days")
        if window is not None and value > window:
            raise ValueError(
                f"min_history_days ({value}) exceeds iv_window_days ({window}); "
                "the rank could never be reported"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached; call `get_settings.cache_clear()` in tests."""
    return Settings()
