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

    @field_validator("options_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in {"yfinance", "fixture"}:
            raise ValueError(f"unknown provider {value!r}; expected 'yfinance' or 'fixture'")
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
