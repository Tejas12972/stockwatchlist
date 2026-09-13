"""API contract tests.

Run against the real app with the provider and database swapped for test
doubles. Half of these assert the error contract: SPEC.md M3 requires that
errors are typed and useful, never a bare 500.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient


class TestHealth:
    def test_reports_the_provider_and_database(self, api_client: TestClient) -> None:
        body = api_client.get("/health").json()
        assert body["status"] == "ok"
        assert body["provider"] == "fixture"
        assert body["database_reachable"] is True


class TestWatchlist:
    def test_starts_empty(self, api_client: TestClient) -> None:
        assert api_client.get("/watchlist").json()["items"] == []

    def test_add_then_list(self, api_client: TestClient) -> None:
        created = api_client.post("/watchlist", json={"symbol": "aapl", "note": "core"})
        assert created.status_code == 201
        assert created.json()["symbol"] == "AAPL"

        items = api_client.get("/watchlist").json()["items"]
        assert [i["symbol"] for i in items] == ["AAPL"]
        assert items[0]["stored_days"] == 0
        assert items[0]["note"] == "core"

    def test_delete_is_a_soft_delete(self, api_client: TestClient) -> None:
        api_client.post("/watchlist", json={"symbol": "AAPL"})
        assert api_client.delete("/watchlist/AAPL").status_code == 204
        assert api_client.get("/watchlist").json()["items"] == []

        inactive = api_client.get("/watchlist", params={"include_inactive": True}).json()
        assert [i["symbol"] for i in inactive["items"]] == ["AAPL"]

    def test_deleting_an_untracked_symbol_is_a_typed_404(self, api_client: TestClient) -> None:
        response = api_client.delete("/watchlist/ZZZZ")
        assert response.status_code == 404
        assert response.json()["code"] == "not_on_watchlist"

    def test_an_empty_symbol_is_rejected_by_validation(self, api_client: TestClient) -> None:
        assert api_client.post("/watchlist", json={"symbol": ""}).status_code == 422


class TestQuoteAndChain:
    def test_quote(self, api_client: TestClient) -> None:
        body = api_client.get("/quote/AAPL").json()
        assert body["ticker"] == "AAPL"
        assert body["price"] > 0

    def test_chain_defaults_to_the_nearest_expiry(self, api_client: TestClient) -> None:
        body = api_client.get("/chain/AAPL").json()
        assert body["expiry"] == min(body["available_expiries"])

    def test_chain_carries_the_pricing_assumptions(self, api_client: TestClient) -> None:
        """Without these the returned IV cannot be interpreted."""
        body = api_client.get("/chain/AAPL").json()
        assert body["risk_free_rate"] == pytest.approx(0.04)
        assert body["dividend_yield"] == pytest.approx(0.0)
        assert body["time_to_expiry"] > 0

    def test_contracts_carry_our_own_greeks(self, api_client: TestClient) -> None:
        body = api_client.get("/chain/AAPL", params={"expiry": "2027-01-15"}).json()
        solved = [c for c in body["contracts"] if c["iv"] is not None]
        assert solved
        for contract in solved[:20]:
            assert contract["delta"] is not None
            assert contract["vega"] is not None
            assert contract["iv_status"] == "ok"

    def test_unsolved_contracts_are_null_with_a_reason_not_zero(
        self, api_client: TestClient
    ) -> None:
        """The central honesty guarantee, asserted at the HTTP boundary."""
        body = api_client.get("/chain/AAPL", params={"expiry": "2027-01-15"}).json()
        unsolved = [c for c in body["contracts"] if c["iv"] is None]
        assert unsolved, "the capture contains stale deep-ITM quotes"
        for contract in unsolved:
            assert contract["iv"] is None, "must be null, never 0.0"
            assert contract["iv_status"] not in ("", "ok")
            assert contract["delta"] is None

    def test_right_filter(self, api_client: TestClient) -> None:
        body = api_client.get("/chain/AAPL", params={"right": "put"}).json()
        assert {c["right"] for c in body["contracts"]} == {"put"}

    def test_near_the_money_filter(self, api_client: TestClient) -> None:
        body = api_client.get("/chain/AAPL", params={"near_the_money": 5}).json()
        assert body["contracts"]
        for contract in body["contracts"]:
            assert abs(contract["moneyness"] - 1.0) <= 0.05

    def test_summary_reports_the_solve_rate(self, api_client: TestClient) -> None:
        summary = api_client.get("/chain/AAPL").json()["summary"]
        assert summary["contracts"] > 0
        assert 0.0 < summary["solve_rate"] <= 1.0
        assert summary["solved"] + sum(summary["unsolved_reasons"].values()) == summary["contracts"]

    def test_disclaimer_is_on_the_response(self, api_client: TestClient) -> None:
        assert "Not investment advice" in api_client.get("/chain/AAPL").json()["disclaimer"]


class TestErrorContract:
    def test_unknown_ticker_is_404_not_500(self, api_client: TestClient) -> None:
        response = api_client.get("/chain/NOSUCHTICKER")
        assert response.status_code == 404
        assert response.json()["code"] == "unknown_ticker"

    def test_unlisted_expiry_is_404_with_the_alternatives(self, api_client: TestClient) -> None:
        """A client should be able to correct itself in one round trip."""
        response = api_client.get("/chain/AAPL", params={"expiry": "1999-01-01"})
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "expiry_not_found"
        assert body["detail"]["available_expiries"]

    def test_every_error_body_has_the_same_shape(self, api_client: TestClient) -> None:
        for response in (
            api_client.get("/chain/NOSUCHTICKER"),
            api_client.get("/chain/AAPL", params={"expiry": "1999-01-01"}),
            api_client.delete("/watchlist/ZZZZ"),
        ):
            body = response.json()
            assert set(body) == {"code", "message", "detail"}
            assert body["message"]

    def test_bad_query_parameters_are_422_not_500(self, api_client: TestClient) -> None:
        assert api_client.get("/chain/AAPL", params={"right": "sideways"}).status_code == 422
        assert api_client.get("/chain/AAPL", params={"near_the_money": -5}).status_code == 422


class TestIVRank:
    def test_returns_200_with_null_rather_than_erroring(self, api_client: TestClient) -> None:
        """Insufficient history is the correct answer, not a failure."""
        response = api_client.get("/ivrank/AAPL")
        assert response.status_code == 200
        body = response.json()
        assert body["rank"] is None
        assert body["reason"]

    def test_reason_is_renderable(self, api_client: TestClient) -> None:
        api_client.post("/snapshot/AAPL")
        body = api_client.get("/ivrank/AAPL").json()
        assert body["reason"] == "insufficient history (1/20 days)"
        assert body["days_available"] == 1
        assert body["days_required"] == 20
        assert body["current_iv"] is not None, "today's vol is known even if its rank is not"


class TestSnapshot:
    def test_creates_then_refreshes(self, api_client: TestClient) -> None:
        first = api_client.post("/snapshot/AAPL").json()
        assert first["created"] is True
        assert first["contracts_written"] > 0

        second = api_client.post("/snapshot/AAPL").json()
        assert second["created"] is False
        assert second["contracts_written"] == first["contracts_written"]

    def test_records_the_solve_rate_and_atm_vol(self, api_client: TestClient) -> None:
        body = api_client.post("/snapshot/AAPL").json()
        assert 0 < body["solve_rate"] <= 1
        assert body["atm_iv_30d"] is not None
        assert body["expiries"]

    def test_snapshotting_registers_the_ticker(self, api_client: TestClient) -> None:
        api_client.post("/snapshot/SPY")
        items = api_client.get("/watchlist").json()["items"]
        assert [i["symbol"] for i in items] == ["SPY"]
        assert items[0]["stored_days"] == 1


class TestPayoff:
    BULL_SPREAD = {
        "spot": 100.0,
        "time_to_expiry": 0.25,
        "points": 51,
        "legs": [
            {"kind": "call", "quantity": 1, "premium": 5.0, "strike": 100, "volatility": 0.25},
            {"kind": "call", "quantity": -1, "premium": 1.5, "strike": 110, "volatility": 0.23},
        ],
    }

    def test_returns_both_curves(self, api_client: TestClient) -> None:
        body = api_client.post("/payoff", json=self.BULL_SPREAD).json()
        assert len(body["underlying_prices"]) == 51
        assert len(body["pnl_at_expiry"]) == 51
        assert len(body["pnl_today"]) == 51
        assert body["today_unavailable_reason"] is None

    def test_reports_the_structure(self, api_client: TestClient) -> None:
        body = api_client.post("/payoff", json=self.BULL_SPREAD).json()
        assert body["net_cost"] == pytest.approx(350.0)
        assert body["max_profit"] == pytest.approx(650.0)
        assert body["max_loss"] == pytest.approx(-350.0)
        assert body["breakevens"] == pytest.approx([103.5])
        assert body["unlimited_profit"] is False

    def test_unlimited_loss_is_null_not_a_big_number(self, api_client: TestClient) -> None:
        body = api_client.post(
            "/payoff",
            json={
                "spot": 100.0,
                "time_to_expiry": 0.25,
                "legs": [
                    {
                        "kind": "call",
                        "quantity": -1,
                        "premium": 5.0,
                        "strike": 100,
                        "volatility": 0.25,
                    }
                ],
            },
        ).json()
        assert body["max_loss"] is None
        assert body["unlimited_loss"] is True

    def test_volatility_is_solved_from_the_chain_when_omitted(self, api_client: TestClient) -> None:
        body = api_client.post(
            "/payoff",
            json={
                "ticker": "AAPL",
                "expiry": "2027-01-15",
                "points": 21,
                "legs": [{"kind": "call", "quantity": 1, "premium": 19.0, "strike": 340}],
            },
        ).json()
        assert body["pnl_today"] is not None, "the service should have found a vol"
        assert body["spot"] > 0

    def test_missing_volatility_omits_the_today_curve_with_a_reason(
        self, api_client: TestClient
    ) -> None:
        body = api_client.post(
            "/payoff",
            json={
                "spot": 100.0,
                "time_to_expiry": 0.25,
                "legs": [{"kind": "call", "quantity": 1, "premium": 5.0, "strike": 100}],
            },
        ).json()
        assert body["pnl_today"] is None
        assert "implied volatility" in body["today_unavailable_reason"]
        assert body["pnl_at_expiry"], "the expiry curve needs no volatility"

    def test_no_spot_and_no_ticker_is_a_typed_400(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/payoff",
            json={"legs": [{"kind": "call", "quantity": 1, "premium": 5.0, "strike": 100}]},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "spot_required"

    def test_zero_quantity_is_422(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/payoff",
            json={
                "spot": 100.0,
                "legs": [{"kind": "call", "quantity": 0, "premium": 5.0, "strike": 100}],
            },
        )
        assert response.status_code == 422

    def test_a_stock_leg_with_a_strike_is_a_typed_400(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/payoff",
            json={
                "spot": 100.0,
                "legs": [{"kind": "stock", "quantity": 100, "premium": 100.0, "strike": 95}],
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_input"

    def test_empty_leg_list_is_422(self, api_client: TestClient) -> None:
        assert api_client.post("/payoff", json={"spot": 100.0, "legs": []}).status_code == 422


class TestOpenAPI:
    def test_docs_are_browsable(self, api_client: TestClient) -> None:
        """SPEC.md M3 done-when: /docs is browsable."""
        assert api_client.get("/docs").status_code == 200
        assert api_client.get("/redoc").status_code == 200

    def test_every_endpoint_is_documented(self, api_client: TestClient) -> None:
        paths = api_client.get("/openapi.json").json()["paths"]
        assert {
            "/health",
            "/watchlist",
            "/watchlist/{ticker}",
            "/quote/{ticker}",
            "/chain/{ticker}",
            "/ivrank/{ticker}",
            "/snapshot/{ticker}",
            "/payoff",
        } <= set(paths)

    def test_nullable_analytics_are_declared_nullable(self, api_client: TestClient) -> None:
        """Clients must be told that iv and rank can be null."""
        schemas = api_client.get("/openapi.json").json()["components"]["schemas"]
        assert "anyOf" in schemas["ContractOut"]["properties"]["iv"]
        assert "anyOf" in schemas["IVRankResponse"]["properties"]["rank"]


class TestEndToEnd:
    def test_the_full_loop(self, api_client: TestClient) -> None:
        """Add a symbol, snapshot it, read the chain, ask for a rank honestly."""
        assert api_client.post("/watchlist", json={"symbol": "AAPL"}).status_code == 201
        assert api_client.post("/snapshot/AAPL").json()["contracts_written"] > 0

        chain = api_client.get("/chain/AAPL").json()
        assert chain["summary"]["solved"] > 0

        rank = api_client.get("/ivrank/AAPL").json()
        assert rank["rank"] is None
        assert "insufficient history" in rank["reason"]

        item = api_client.get("/watchlist").json()["items"][0]
        assert item["stored_days"] == 1
        assert item["first_snapshot"] == item["last_snapshot"]
        assert date.fromisoformat(item["last_snapshot"])


class TestScreenEndpoint:
    def test_reports_how_many_were_screened(self, api_client: TestClient) -> None:
        """`screened` distinguishes "nothing flagged" from "nothing checked"."""
        api_client.post("/watchlist", json={"symbol": "AAPL"})
        api_client.post("/snapshot/AAPL")

        body = api_client.get("/screen").json()
        assert body["screened"] == 1
        assert body["hits"] == []
        assert any("insufficient history" in note for note in body["notes"])

    def test_thresholds_are_echoed_back(self, api_client: TestClient) -> None:
        body = api_client.get("/screen", params={"high": 80, "low": 20}).json()
        assert body["thresholds"]["high_iv_rank"] == 80
        assert body["thresholds"]["low_iv_rank"] == 20

    def test_invalid_thresholds_are_422(self, api_client: TestClient) -> None:
        assert api_client.get("/screen", params={"high": 500}).status_code == 422
        assert api_client.get("/screen", params={"multiple": 0.5}).status_code == 422

    def test_carries_the_disclaimer(self, api_client: TestClient) -> None:
        assert "Not investment advice" in api_client.get("/screen").json()["disclaimer"]

    def test_empty_watchlist_screens_zero_without_erroring(self, api_client: TestClient) -> None:
        body = api_client.get("/screen").json()
        assert body["screened"] == 0
        assert body["hits"] == []


class TestEarningsEndpoint:
    def test_unknown_is_200_with_known_false_not_a_404(self, api_client: TestClient) -> None:
        """An unknown date is a different answer from no date being scheduled."""
        response = api_client.get("/earnings/AAPL")
        assert response.status_code == 200
        body = response.json()
        assert body["known"] is False
        assert body["next_earnings"] is None
        assert body["days_away"] is None
