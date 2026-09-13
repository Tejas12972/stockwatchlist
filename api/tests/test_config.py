"""Settings validation.

The pricing assumptions live in config precisely so they can be stated in the
README. These tests keep them from silently accepting nonsense.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from options_tool.config import Settings


class TestDefaults:
    def test_a_clean_clone_works_with_no_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in list(Settings.model_fields):
            monkeypatch.delenv(key.upper(), raising=False)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.options_provider == "yfinance"
        assert settings.options_min_history_days == 20
        assert settings.options_iv_window_days == 252
        assert settings.options_fetch_risk_free_rate is False


class TestValidation:
    @pytest.mark.parametrize("name", ["bloomberg", "", "yahoo"])
    def test_unknown_provider_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError, match="unknown provider"):
            Settings(options_provider=name, _env_file=None)  # type: ignore[call-arg]

    @pytest.mark.parametrize("name", ["YFINANCE", " Fixture ", "fixture"])
    def test_provider_name_is_normalised(self, name: str) -> None:
        assert Settings(options_provider=name, _env_file=None).options_provider in (  # type: ignore[call-arg]
            "yfinance",
            "fixture",
        )

    def test_min_history_cannot_exceed_the_window(self) -> None:
        """Otherwise the rank could never be reported at all."""
        with pytest.raises(ValidationError, match="could never be reported"):
            Settings(  # type: ignore[call-arg]
                options_iv_window_days=10, options_min_history_days=50, _env_file=None
            )

    @pytest.mark.parametrize("rate", [-0.5, 1.5])
    def test_absurd_risk_free_rates_rejected(self, rate: float) -> None:
        with pytest.raises(ValidationError):
            Settings(options_risk_free_rate=rate, _env_file=None)  # type: ignore[call-arg]

    @pytest.mark.parametrize("yield_", [-0.01, 0.9])
    def test_absurd_dividend_yields_rejected(self, yield_: float) -> None:
        with pytest.raises(ValidationError):
            Settings(options_dividend_yield=yield_, _env_file=None)  # type: ignore[call-arg]

    def test_min_history_must_be_at_least_one(self) -> None:
        with pytest.raises(ValidationError):
            Settings(options_min_history_days=0, _env_file=None)  # type: ignore[call-arg]


class TestEnvironmentOverrides:
    def test_env_vars_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPTIONS_PROVIDER", "fixture")
        monkeypatch.setenv("OPTIONS_MIN_HISTORY_DAYS", "45")
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.options_provider == "fixture"
        assert settings.options_min_history_days == 45

    def test_no_secret_has_a_default_value(self) -> None:
        """Keys must come from the environment, never be baked into the source."""
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.polygon_api_key is None
        assert settings.tradier_api_key is None
