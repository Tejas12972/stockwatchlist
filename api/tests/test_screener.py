"""The screener.

It narrows a watchlist; it does not rank opportunities. These tests hold it to
that, and to the same refusal-to-guess discipline as the rest of the project:
with too little history it says so rather than flagging on a baseline of six
days.
"""

from __future__ import annotations

import pytest

from options_tool.analytics.screener import (
    MIN_ACTIVITY_HISTORY,
    ScreenFlag,
    ScreenInput,
    screen,
)


def make_input(
    ticker: str = "AAPL",
    iv_rank: float | None = 50.0,
    volume_today: int | None = 1_000,
    volume_history: list[int] | None = None,
    open_interest_today: int | None = 5_000,
    open_interest_history: list[int] | None = None,
    days_to_earnings: int | None = None,
    days_to_near_expiry: int | None = None,
) -> ScreenInput:
    return ScreenInput(
        ticker=ticker,
        iv_rank=iv_rank,
        iv_rank_reason="ranked against stored history" if iv_rank else "insufficient history",
        current_iv=0.25,
        volume_today=volume_today,
        volume_history=volume_history if volume_history is not None else [1_000] * 20,
        open_interest_today=open_interest_today,
        open_interest_history=(
            open_interest_history if open_interest_history is not None else [5_000] * 20
        ),
        days_to_earnings=days_to_earnings,
        days_to_near_expiry=days_to_near_expiry,
    )


class TestIVRankFlags:
    def test_high_rank_flagged(self) -> None:
        hits = screen([make_input(iv_rank=85.0)])
        assert hits[0].flags == [ScreenFlag.HIGH_IV_RANK]

    def test_low_rank_flagged(self) -> None:
        hits = screen([make_input(iv_rank=12.0)])
        assert hits[0].flags == [ScreenFlag.LOW_IV_RANK]

    def test_middling_rank_not_flagged(self) -> None:
        assert screen([make_input(iv_rank=50.0)]) == []

    @pytest.mark.parametrize("rank", [70.0, 30.0])
    def test_thresholds_are_inclusive(self, rank: float) -> None:
        assert screen([make_input(iv_rank=rank)])

    def test_thresholds_are_configurable(self) -> None:
        assert screen([make_input(iv_rank=60.0)]) == []
        assert screen([make_input(iv_rank=60.0)], high_iv_rank=55.0)


class TestUnavailableRankIsReportedNotHidden:
    def test_missing_rank_produces_no_flag(self) -> None:
        """No history is not a signal, in either direction."""
        assert screen([make_input(iv_rank=None)]) == []

    def test_but_a_symbol_with_another_flag_carries_the_note(self) -> None:
        """Otherwise "not flagged" and "not checkable" look identical."""
        hits = screen([make_input(iv_rank=None, volume_today=10_000, volume_history=[1_000] * 20)])
        assert hits[0].flags == [ScreenFlag.UNUSUAL_VOLUME]
        assert any("IV rank unavailable" in note for note in hits[0].notes)


class TestActivityFlags:
    def test_volume_well_above_median_flagged(self) -> None:
        hits = screen([make_input(volume_today=5_000, volume_history=[1_000] * 20)])
        assert ScreenFlag.UNUSUAL_VOLUME in hits[0].flags
        assert hits[0].volume_multiple == pytest.approx(5.0)
        assert hits[0].volume_median == pytest.approx(1_000)

    def test_ordinary_volume_not_flagged(self) -> None:
        assert screen([make_input(volume_today=1_100, volume_history=[1_000] * 20)]) == []

    def test_uses_the_median_not_the_mean(self) -> None:
        """Option volume is spiky; one expiry week would poison a mean for weeks."""
        history = [1_000] * 19 + [500_000]
        hits = screen([make_input(volume_today=3_000, volume_history=history)])
        assert ScreenFlag.UNUSUAL_VOLUME in hits[0].flags
        assert hits[0].volume_median == pytest.approx(1_000)

    def test_open_interest_flagged_separately(self) -> None:
        hits = screen([make_input(open_interest_today=50_000, open_interest_history=[5_000] * 20)])
        assert ScreenFlag.UNUSUAL_OPEN_INTEREST in hits[0].flags


class TestTooLittleHistoryToJudgeActivity:
    def test_short_history_produces_no_activity_flag(self) -> None:
        """With six days, "2x the median" describes the sample, not the market."""
        hits = screen([make_input(iv_rank=50.0, volume_today=1_000_000, volume_history=[10] * 5)])
        assert hits == []

    def test_exactly_the_minimum_is_enough(self) -> None:
        hits = screen(
            [
                make_input(
                    volume_today=10_000,
                    volume_history=[1_000] * MIN_ACTIVITY_HISTORY,
                )
            ]
        )
        assert ScreenFlag.UNUSUAL_VOLUME in hits[0].flags

    def test_an_all_zero_baseline_does_not_flag(self) -> None:
        """Any activity against a zero median is infinite growth and meaningless."""
        hits = screen([make_input(volume_today=5, volume_history=[0] * 20)])
        assert hits == []

    def test_missing_today_value_does_not_flag(self) -> None:
        assert screen([make_input(volume_today=None, volume_history=[1_000] * 20)]) == []


class TestEarnings:
    def test_earnings_before_the_near_expiry_is_flagged(self) -> None:
        hits = screen([make_input(days_to_earnings=5, days_to_near_expiry=10)])
        assert ScreenFlag.NEAR_EARNINGS in hits[0].flags

    def test_earnings_after_the_near_expiry_is_not(self) -> None:
        assert screen([make_input(days_to_earnings=30, days_to_near_expiry=10)]) == []

    def test_unknown_earnings_date_is_not_a_flag(self) -> None:
        """None means "we do not know", which must never read as "none scheduled"."""
        assert screen([make_input(days_to_earnings=None, days_to_near_expiry=10)]) == []

    def test_past_earnings_is_not_flagged(self) -> None:
        assert screen([make_input(days_to_earnings=-3, days_to_near_expiry=10)]) == []


class TestOrderingAndOutput:
    def test_more_flags_sort_first(self) -> None:
        busy = make_input(
            ticker="BUSY", iv_rank=90.0, volume_today=9_000, volume_history=[1_000] * 20
        )
        quiet = make_input(ticker="QUIET", iv_rank=95.0)
        assert [hit.ticker for hit in screen([quiet, busy])] == ["BUSY", "QUIET"]

    def test_unflagged_symbols_are_omitted_entirely(self) -> None:
        hits = screen([make_input(ticker="A", iv_rank=50.0), make_input(ticker="B", iv_rank=90.0)])
        assert [hit.ticker for hit in hits] == ["B"]

    def test_summary_is_human_readable(self) -> None:
        hits = screen([make_input(iv_rank=90.0)])
        assert "implied volatility is high" in hits[0].summary

    def test_every_flag_has_an_explanation(self) -> None:
        for flag in ScreenFlag:
            assert flag.explanation
            assert not flag.explanation.endswith(".")

    def test_empty_input(self) -> None:
        assert screen([]) == []
