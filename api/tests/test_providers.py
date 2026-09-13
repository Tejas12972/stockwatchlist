"""The market-data boundary.

The yfinance provider is tested against a fake `yfinance` module injected into
`sys.modules`. That is what lets the retry logic, the NaN handling and -- most
importantly -- the rule that **no vendor greek may cross this boundary** be
covered with sockets disabled.
"""

from __future__ import annotations

import sys
import types
from dataclasses import fields
from datetime import date

import pandas as pd
import pytest

from options_tool.providers import get_provider
from options_tool.providers.base import (
    ExpiryNotFoundError,
    OptionChain,
    OptionQuote,
    ProviderError,
    RateLimitedError,
    UnknownTickerError,
)
from options_tool.providers.fixture_provider import FixtureProvider
from options_tool.providers.yfinance_provider import YFinanceProvider


class TestTheBoundaryItself:
    def test_option_quote_has_nowhere_to_put_a_vendor_greek(self) -> None:
        """SPEC.md section 4, enforced structurally rather than by discipline.

        If this test fails, someone has added a field that lets a vendor's own
        delta or implied volatility into the system, and the central claim of
        the project stops being true.
        """
        names = {f.name for f in fields(OptionQuote)}
        forbidden = {
            "iv",
            "implied_volatility",
            "impliedVolatility",
            "delta",
            "gamma",
            "theta",
            "vega",
            "rho",
            "in_the_money",
        }
        assert names & forbidden == set()

    def test_mid_requires_a_two_sided_book(self) -> None:
        base = {"ticker": "T", "expiry": date(2027, 1, 15), "strike": 100.0, "right": "call"}
        assert OptionQuote(**base, bid=4.0, ask=4.2).mid == pytest.approx(4.1)
        assert OptionQuote(**base, bid=None, ask=4.2).mid is None
        assert OptionQuote(**base, bid=4.0, ask=None).mid is None
        assert OptionQuote(**base, bid=0.0, ask=4.2).mid is None
        assert OptionQuote(**base, bid=5.0, ask=3.0).mid is None, "crossed book has no mid"

    def test_mid_never_silently_borrows_last(self) -> None:
        quote = OptionQuote(
            ticker="T",
            expiry=date(2027, 1, 15),
            strike=100.0,
            right="call",
            bid=None,
            ask=None,
            last=4.1,
        )
        assert quote.mid is None


class TestFixtureProvider:
    def test_replays_the_capture(self, provider: FixtureProvider) -> None:
        chain = provider.get_chain("AAPL", date(2027, 1, 15))
        assert isinstance(chain, OptionChain)
        assert len(chain) > 100
        assert chain.spot > 0

    def test_is_deterministic(self, provider: FixtureProvider) -> None:
        """Same input, same output -- what makes it usable as a test double."""
        first = provider.get_chain("AAPL", date(2027, 1, 15))
        second = provider.get_chain("AAPL", date(2027, 1, 15))
        assert first.quotes == second.quotes
        assert first.as_of == second.as_of

    def test_expiries_are_sorted(self, provider: FixtureProvider) -> None:
        expiries = provider.get_expiries("AAPL")
        assert list(expiries) == sorted(expiries)

    def test_captured_at_is_exposed(self, provider: FixtureProvider) -> None:
        """Callers must be able to show how stale the data is."""
        assert provider.captured_at.year >= 2026

    def test_unknown_ticker(self, provider: FixtureProvider) -> None:
        with pytest.raises(UnknownTickerError, match="Captured tickers"):
            provider.get_quote("NOSUCHTICKER")

    def test_unknown_expiry_lists_the_ones_that_exist(self, provider: FixtureProvider) -> None:
        with pytest.raises(ExpiryNotFoundError) as info:
            provider.get_chain("AAPL", date(1999, 1, 1))
        assert info.value.available

    def test_missing_file_explains_how_to_regenerate(self, tmp_path: object) -> None:
        from pathlib import Path

        with pytest.raises(FileNotFoundError, match="capture-fixture"):
            FixtureProvider(Path(str(tmp_path)) / "nope.json")

    def test_every_captured_quote_has_a_usable_strike(self, provider: FixtureProvider) -> None:
        for expiry in provider.get_expiries("SPY"):
            for quote in provider.get_chain("SPY", expiry).quotes:
                assert quote.strike > 0
                assert quote.right in ("call", "put")


class TestResolveExpiry:
    def test_none_selects_the_nearest(self, provider: FixtureProvider) -> None:
        assert provider.resolve_expiry("AAPL", None) == provider.get_expiries("AAPL")[0]

    def test_an_explicit_listed_expiry_is_honoured(self, provider: FixtureProvider) -> None:
        assert provider.resolve_expiry("AAPL", date(2027, 1, 15)) == date(2027, 1, 15)

    def test_an_unlisted_expiry_raises_with_the_alternatives(
        self, provider: FixtureProvider
    ) -> None:
        with pytest.raises(ExpiryNotFoundError) as info:
            provider.resolve_expiry("AAPL", date(2030, 6, 21))
        assert "Available" in str(info.value)
        assert len(info.value.available) == 3


# --------------------------------------------------------------------------
# yfinance, against a fake vendor module
# --------------------------------------------------------------------------


class FakeFastInfo(dict):
    pass


class FakeChain:
    def __init__(self, calls: pd.DataFrame, puts: pd.DataFrame, underlying: dict) -> None:
        self.calls = calls
        self.puts = puts
        self.underlying = underlying


class FakeTicker:
    """Stands in for `yfinance.Ticker`, including its failure modes."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.calls_made = 0

    @property
    def fast_info(self) -> FakeFastInfo:
        return FakeFastInfo(lastPrice=100.0, previousClose=99.0, currency="USD")

    @property
    def options(self) -> tuple[str, ...]:
        return ("2027-01-15", "2026-09-14")  # deliberately unsorted

    def option_chain(self, expiry: str) -> FakeChain:
        # Includes the vendor columns we must ignore, plus the NaN/None soup a
        # real Yahoo response contains.
        frame = pd.DataFrame(
            [
                {
                    "contractSymbol": "FAKE260914C00100000",
                    "strike": 100.0,
                    "bid": 4.0,
                    "ask": 4.2,
                    "lastPrice": 4.1,
                    "volume": 12,
                    "openInterest": 345,
                    "impliedVolatility": 9.99,
                    "inTheMoney": True,
                },
                {
                    "contractSymbol": "FAKE260914C00105000",
                    "strike": 105.0,
                    "bid": float("nan"),
                    "ask": None,
                    "lastPrice": 2.0,
                    "volume": None,
                    "openInterest": float("nan"),
                    "impliedVolatility": 8.88,
                    "inTheMoney": False,
                },
                {
                    "contractSymbol": "BROKEN",
                    "strike": float("nan"),
                    "bid": 1.0,
                    "ask": 1.2,
                    "lastPrice": 1.1,
                    "volume": 1,
                    "openInterest": 1,
                    "impliedVolatility": 0.5,
                    "inTheMoney": False,
                },
            ]
        )
        return FakeChain(frame, frame.iloc[0:1], {"regularMarketPrice": 101.5})


@pytest.fixture
def fake_yfinance(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("yfinance")
    module.Ticker = FakeTicker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", module)
    return module


class TestYFinanceMapping:
    def test_quote(self, fake_yfinance: types.ModuleType) -> None:
        quote = YFinanceProvider().get_quote("aapl")
        assert quote.ticker == "AAPL"
        assert quote.price == pytest.approx(100.0)
        assert quote.previous_close == pytest.approx(99.0)

    def test_expiries_are_parsed_and_sorted(self, fake_yfinance: types.ModuleType) -> None:
        assert YFinanceProvider().get_expiries("AAPL") == (
            date(2026, 9, 14),
            date(2027, 1, 15),
        )

    def test_vendor_greeks_are_discarded(self, fake_yfinance: types.ModuleType) -> None:
        """The fake reports impliedVolatility=9.99. Nothing may carry it forward."""
        chain = YFinanceProvider().get_chain("AAPL", date(2026, 9, 14))
        for quote in chain.quotes:
            assert not hasattr(quote, "impliedVolatility")
            assert 9.99 not in (quote.bid, quote.ask, quote.last)

    def test_nan_and_none_become_none(self, fake_yfinance: types.ModuleType) -> None:
        chain = YFinanceProvider().get_chain("AAPL", date(2026, 9, 14))
        by_strike = {q.strike: q for q in chain.quotes if q.right == "call"}
        assert by_strike[105.0].bid is None
        assert by_strike[105.0].ask is None
        assert by_strike[105.0].volume is None
        assert by_strike[105.0].open_interest is None

    def test_rows_without_a_strike_are_dropped(self, fake_yfinance: types.ModuleType) -> None:
        """A strike-less row is corrupt, as distinct from merely unquoted."""
        chain = YFinanceProvider().get_chain("AAPL", date(2026, 9, 14))
        assert all(q.contract_symbol != "BROKEN" for q in chain.quotes)

    def test_spot_comes_from_the_chain_response(self, fake_yfinance: types.ModuleType) -> None:
        """Using the embedded spot keeps greeks consistent with their own chain."""
        assert YFinanceProvider().get_chain("AAPL", date(2026, 9, 14)).spot == pytest.approx(101.5)


class TestYFinanceFailureHandling:
    @staticmethod
    def provider_raising(message: str, attempts: list[int]) -> YFinanceProvider:
        class Raiser(FakeTicker):
            def option_chain(self, expiry: str) -> FakeChain:
                attempts.append(1)
                raise Exception(message)

            @property
            def options(self) -> tuple[str, ...]:
                attempts.append(1)
                raise Exception(message)

        module = types.ModuleType("yfinance")
        module.Ticker = Raiser  # type: ignore[attr-defined]
        sys.modules["yfinance"] = module
        return YFinanceProvider(max_retries=3, backoff_seconds=0.0)

    def test_rate_limits_are_retried_then_reported_as_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts: list[int] = []
        provider = self.provider_raising("Too Many Requests", attempts)

        with pytest.raises(RateLimitedError, match="--provider fixture"):
            provider.get_expiries("AAPL")
        assert len(attempts) == 3, "should have retried up to max_retries"

    def test_invalid_crumb_is_treated_as_a_rate_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Yahoo's throttling surfaces as a crumb error; it is still transient."""
        attempts: list[int] = []
        provider = self.provider_raising("Invalid Crumb", attempts)
        with pytest.raises(RateLimitedError):
            provider.get_expiries("AAPL")
        assert len(attempts) == 3

    def test_other_failures_are_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A schema change will not fix itself by waiting."""
        attempts: list[int] = []
        provider = self.provider_raising("KeyError: 'strike'", attempts)

        with pytest.raises(ProviderError) as info:
            provider.get_expiries("AAPL")
        assert not isinstance(info.value, RateLimitedError)
        assert len(attempts) == 1, "non-transient failures must fail fast"

    def test_a_priceless_quote_is_an_unknown_ticker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Priceless(FakeTicker):
            @property
            def fast_info(self) -> FakeFastInfo:
                return FakeFastInfo(lastPrice=None)

        module = types.ModuleType("yfinance")
        module.Ticker = Priceless  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)

        with pytest.raises(UnknownTickerError):
            YFinanceProvider().get_quote("NOPE")

    def test_no_listed_expiries_is_an_unknown_ticker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class NoOptions(FakeTicker):
            @property
            def options(self) -> tuple[str, ...]:
                return ()

        module = types.ModuleType("yfinance")
        module.Ticker = NoOptions  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)

        with pytest.raises(UnknownTickerError):
            YFinanceProvider().get_expiries("BRK-A")


class TestProviderFactory:
    def test_builds_the_fixture_provider(self, settings: object) -> None:
        assert get_provider("fixture").name == "fixture"

    def test_builds_the_yfinance_provider_without_importing_it(self, settings: object) -> None:
        """Constructing the provider must not pull in the vendor library."""
        assert get_provider("yfinance").name == "yfinance"

    def test_unknown_provider_name(self, settings: object) -> None:
        with pytest.raises(ValueError, match="unknown provider"):
            get_provider("bloomberg")

    def test_defaults_to_the_configured_provider(self, settings: object) -> None:
        assert get_provider().name == "fixture"


class TestRiskFreeRate:
    def test_uses_the_configured_constant_by_default(self, settings) -> None:  # type: ignore[no-untyped-def]
        from options_tool.providers.rates import resolve_risk_free_rate

        assert resolve_risk_free_rate(settings) == pytest.approx(0.04)

    def test_a_failed_fetch_falls_back_rather_than_aborting(
        self,
        settings,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        from options_tool.providers.rates import resolve_risk_free_rate

        class Broken:
            def __init__(self, symbol: str) -> None:
                raise RuntimeError("network is down")

        module = types.ModuleType("yfinance")
        module.Ticker = Broken  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)
        object.__setattr__(settings, "options_fetch_risk_free_rate", True)

        assert resolve_risk_free_rate(settings) == pytest.approx(0.04)

    def test_an_implausible_rate_is_rejected(
        self,
        settings,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        """A garbage quote must not silently reprice every option in the tool."""
        from options_tool.providers.rates import resolve_risk_free_rate

        class Absurd:
            def __init__(self, symbol: str) -> None:
                pass

            @property
            def fast_info(self) -> FakeFastInfo:
                return FakeFastInfo(lastPrice=9999.0)

        module = types.ModuleType("yfinance")
        module.Ticker = Absurd  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)
        object.__setattr__(settings, "options_fetch_risk_free_rate", True)

        assert resolve_risk_free_rate(settings) == pytest.approx(0.04)

    def test_a_plausible_rate_is_used_and_converted_from_percent(
        self,
        settings,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        from options_tool.providers.rates import resolve_risk_free_rate

        class Bill:
            def __init__(self, symbol: str) -> None:
                assert symbol == "^IRX"

            @property
            def fast_info(self) -> FakeFastInfo:
                return FakeFastInfo(lastPrice=4.25)

        module = types.ModuleType("yfinance")
        module.Ticker = Bill  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "yfinance", module)
        object.__setattr__(settings, "options_fetch_risk_free_rate", True)

        assert resolve_risk_free_rate(settings) == pytest.approx(0.0425)
