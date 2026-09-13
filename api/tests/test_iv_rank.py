"""IV rank and percentile.

The arithmetic is one line. These tests are overwhelmingly about the cases where
the tool must **refuse to produce a number** -- which is the actual requirement
(SPEC.md section 3).
"""

from __future__ import annotations

from datetime import date

import pytest

from options_tool.analytics.iv_rank import (
    IVObservation,
    IVRankStatus,
    compute_iv_rank,
)
from tests.conftest import make_observations


class TestInsufficientHistory:
    """The headline behaviour: no rank until the window is met."""

    @pytest.mark.parametrize("days", [0, 1, 2, 5, 19])
    def test_below_the_minimum_returns_none(self, days: int) -> None:
        result = compute_iv_rank(make_observations([0.2 + i * 0.01 for i in range(days)]))
        assert result.rank is None
        assert result.percentile is None
        assert not result.ok

    def test_exactly_at_the_minimum_produces_a_rank(self) -> None:
        result = compute_iv_rank(
            make_observations([0.2 + i * 0.01 for i in range(20)]), min_history_days=20
        )
        assert result.ok
        assert result.rank is not None

    def test_reason_is_renderable_verbatim(self) -> None:
        """The UI shows this string as-is; it must read as a sentence."""
        result = compute_iv_rank(make_observations([0.2, 0.25, 0.3]), min_history_days=20)
        assert result.reason == "insufficient history (3/20 days)"
        assert result.status is IVRankStatus.INSUFFICIENT_HISTORY
        assert result.days_available == 3
        assert result.days_required == 20

    def test_current_iv_is_still_reported(self) -> None:
        """Today's vol is known even when its rank is not -- show what we have."""
        result = compute_iv_rank(make_observations([0.2, 0.25, 0.31]), min_history_days=20)
        assert result.current_iv == pytest.approx(0.31)
        assert result.rank is None

    def test_the_arithmetic_would_have_succeeded(self) -> None:
        """Proves the None is a deliberate refusal, not a computation failure."""
        values = [0.10, 0.20, 0.30]
        refused = compute_iv_rank(make_observations(values), min_history_days=20)
        allowed = compute_iv_rank(make_observations(values), min_history_days=3)
        assert refused.rank is None
        assert allowed.rank == pytest.approx(100.0)


class TestRankArithmetic:
    def test_current_at_the_maximum_is_one_hundred(self) -> None:
        result = compute_iv_rank(make_observations([0.1] * 19 + [0.5]), min_history_days=20)
        assert result.rank == pytest.approx(100.0)

    def test_current_at_the_minimum_is_zero(self) -> None:
        result = compute_iv_rank(make_observations([0.5] * 19 + [0.1]), min_history_days=20)
        assert result.rank == pytest.approx(0.0)

    def test_midpoint_is_fifty(self) -> None:
        values = [0.10] + [0.30] * 18 + [0.20]
        result = compute_iv_rank(make_observations(values), min_history_days=20)
        assert result.rank == pytest.approx(50.0)

    def test_matches_the_formula_exactly(self) -> None:
        values = [0.12, 0.34, 0.21, 0.45, 0.19] * 5
        result = compute_iv_rank(make_observations(values), min_history_days=20)
        expected = (values[-1] - min(values)) / (max(values) - min(values)) * 100
        assert result.rank == pytest.approx(expected)
        assert result.iv_min == pytest.approx(min(values))
        assert result.iv_max == pytest.approx(max(values))
        assert result.iv_mean == pytest.approx(sum(values) / len(values))

    def test_rank_is_always_within_zero_and_one_hundred(self) -> None:
        import random

        rng = random.Random(20260913)
        for _ in range(50):
            values = [rng.uniform(0.05, 1.5) for _ in range(40)]
            result = compute_iv_rank(make_observations(values), min_history_days=20)
            assert result.rank is not None
            assert 0.0 <= result.rank <= 100.0


class TestPercentile:
    """Percentile answers a different question from rank, and is more robust."""

    def test_percentile_counts_days_at_or_below_today(self) -> None:
        values = [0.1, 0.2, 0.3, 0.4] * 5 + [0.25]
        result = compute_iv_rank(make_observations(values), min_history_days=20)
        expected = sum(1 for v in values if v <= values[-1]) / len(values) * 100
        assert result.percentile == pytest.approx(expected)

    def test_a_single_outlier_moves_rank_but_barely_moves_percentile(self) -> None:
        """The reason both are reported rather than just rank."""
        ordinary = [0.15 + 0.002 * i for i in range(24)]
        with_spike = [1.20, *ordinary]

        base = compute_iv_rank(make_observations(ordinary), min_history_days=20)
        spiked = compute_iv_rank(make_observations(with_spike), min_history_days=20)

        assert base.rank is not None and spiked.rank is not None
        assert base.percentile is not None and spiked.percentile is not None
        assert base.rank - spiked.rank > 50, "one outlier should crush the rank"
        assert abs(base.percentile - spiked.percentile) < 10, "percentile should barely move"


class TestDegenerateCases:
    def test_flat_history_is_undefined_not_fifty(self) -> None:
        """0/0 has no answer. Convention says 50; convention is inventing data."""
        result = compute_iv_rank(make_observations([0.25] * 30), min_history_days=20)
        assert result.rank is None
        assert result.status is IVRankStatus.DEGENERATE_RANGE
        assert result.current_iv == pytest.approx(0.25)

    def test_no_observations_at_all(self) -> None:
        result = compute_iv_rank([])
        assert result.rank is None
        assert result.status is IVRankStatus.NO_CURRENT_IV
        assert result.days_available == 0

    def test_non_positive_values_are_discarded_not_zero_filled(self) -> None:
        """A day with no ATM vol must not contribute an artificial minimum."""
        values = [0.2 + 0.01 * i for i in range(25)]
        polluted = compute_iv_rank(make_observations([*values, 0.0]), min_history_days=20)
        clean = compute_iv_rank(make_observations(values), min_history_days=20)
        assert polluted.iv_min == clean.iv_min
        assert polluted.days_available == clean.days_available


class TestWindow:
    def test_only_the_trailing_window_is_ranked(self) -> None:
        """An ancient extreme must age out rather than anchor the rank forever."""
        recent = [0.20 + 0.001 * i for i in range(60)]
        with_ancient_spike = [5.0, *recent]

        result = compute_iv_rank(make_observations(with_ancient_spike), window_days=30)

        assert result.iv_max == pytest.approx(max(recent[-30:]))
        assert result.iv_max < 1.0, "the spike must have fallen out of the window"
        assert result.days_available == 30

    def test_window_boundaries_are_reported(self) -> None:
        result = compute_iv_rank(
            make_observations([0.2 + 0.001 * i for i in range(25)], start=date(2026, 3, 1)),
            min_history_days=20,
        )
        assert result.first_observed == date(2026, 3, 1)
        assert result.last_observed == date(2026, 3, 25)
        assert result.window_days == 252

    def test_observations_are_sorted_regardless_of_input_order(self) -> None:
        ordered = make_observations([0.1 + 0.01 * i for i in range(25)])
        shuffled = list(reversed(ordered))
        assert compute_iv_rank(ordered, min_history_days=20).rank == pytest.approx(
            compute_iv_rank(shuffled, min_history_days=20).rank
        )

    def test_latest_observation_by_date_is_the_current_one(self) -> None:
        observations = [
            IVObservation(date(2026, 5, 3), 0.9),
            IVObservation(date(2026, 5, 1), 0.1),
            IVObservation(date(2026, 5, 2), 0.5),
        ]
        result = compute_iv_rank(observations, min_history_days=3)
        assert result.current_iv == pytest.approx(0.9)
        assert result.rank == pytest.approx(100.0)
