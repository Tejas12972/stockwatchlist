"""HTTP routes.

Thin by design: each route resolves inputs, calls the same analytics the CLI
calls, and maps the result onto a Pydantic model. Nothing is computed here that
is not also available offline, which is what keeps the API and the CLI from
drifting apart.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from options_tool import __version__
from options_tool.analytics.chain import build_chain_frame, summarise_chain
from options_tool.analytics.payoff import Leg, LegKind, build_payoff_curve
from options_tool.analytics.screener import screen
from options_tool.api.deps import get_api_settings, get_market_provider, get_session
from options_tool.api.errors import APIError
from options_tool.api.schemas import (
    ChainResponse,
    ChainSummary,
    ContractOut,
    EarningsResponse,
    HealthResponse,
    IVRankResponse,
    LegIn,
    PayoffRequest,
    PayoffResponse,
    QuoteResponse,
    ScreenHitOut,
    ScreenResponse,
    SnapshotResponse,
    WatchlistAddRequest,
    WatchlistItem,
    WatchlistResponse,
)
from options_tool.config import Settings
from options_tool.db.queries import (
    add_to_watchlist,
    history_depth,
    iv_rank_for,
    list_watchlist,
    remove_from_watchlist,
    screen_inputs,
)
from options_tool.db.snapshot import snapshot_ticker
from options_tool.providers.base import MarketDataProvider
from options_tool.providers.rates import resolve_risk_free_rate

logger = logging.getLogger(__name__)

router = APIRouter()

# Annotated dependencies rather than `= Depends(...)` defaults: the call then
# happens inside the annotation rather than at import time, which is both the
# current FastAPI idiom and what keeps these signatures honest to a type checker.
SessionDep = Annotated[Session, Depends(get_session)]
ProviderDep = Annotated[MarketDataProvider, Depends(get_market_provider)]
SettingsDep = Annotated[Settings, Depends(get_api_settings)]


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------


@router.get("/live", tags=["meta"], summary="Liveness — is the process up?")
def live() -> dict[str, str]:
    """Cheap and dependency-free, for a platform restart check.

    Deliberately separate from /health: a readiness probe that touches the
    database will restart a container for a *storage* problem that restarting
    cannot fix, turning a degraded service into a crash loop.
    """
    return {"status": "alive"}


@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health(session: SessionDep, provider: ProviderDep) -> HealthResponse:
    """Readiness: can this instance actually serve?

    Reports the things that genuinely break — the database and which provider is
    configured — and whether the snapshot scheduler is running, so a deployment
    that silently stopped accumulating history is visible rather than something
    you notice weeks later when IV rank never arrives.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from options_tool.config import get_settings  # noqa: PLC0415

    try:
        session.execute(text("SELECT 1"))
        database_reachable = True
    except Exception:
        logger.exception("health check could not reach the database")
        database_reachable = False

    settings = get_settings()
    return HealthResponse(
        status="ok" if database_reachable else "degraded",
        version=__version__,
        provider=provider.name,
        database_reachable=database_reachable,
        snapshot_scheduler=settings.options_snapshot_enabled,
        snapshot_at=settings.options_snapshot_at if settings.options_snapshot_enabled else None,
    )


# --------------------------------------------------------------------------
# watchlist
# --------------------------------------------------------------------------


@router.get("/watchlist", response_model=WatchlistResponse, tags=["watchlist"])
def get_watchlist(
    session: SessionDep,
    include_inactive: Annotated[bool, Query(description="Include removed symbols.")] = False,
) -> WatchlistResponse:
    items = []
    for ticker in list_watchlist(session, include_inactive=include_inactive):
        days, first, last = history_depth(session, ticker.symbol)
        items.append(
            WatchlistItem(
                symbol=ticker.symbol,
                active=ticker.active,
                note=ticker.note,
                stored_days=days,
                first_snapshot=first,
                last_snapshot=last,
            )
        )
    return WatchlistResponse(items=items)


@router.post(
    "/watchlist",
    response_model=WatchlistItem,
    status_code=status.HTTP_201_CREATED,
    tags=["watchlist"],
)
def post_watchlist(payload: WatchlistAddRequest, session: SessionDep) -> WatchlistItem:
    ticker = add_to_watchlist(session, payload.symbol, payload.note)
    days, first, last = history_depth(session, ticker.symbol)
    return WatchlistItem(
        symbol=ticker.symbol,
        active=ticker.active,
        note=ticker.note,
        stored_days=days,
        first_snapshot=first,
        last_snapshot=last,
    )


@router.delete("/watchlist/{ticker}", status_code=status.HTTP_204_NO_CONTENT, tags=["watchlist"])
def delete_watchlist(ticker: str, session: SessionDep) -> None:
    if not remove_from_watchlist(session, ticker):
        raise APIError(
            "not_on_watchlist",
            f"{ticker.upper()} is not on the watchlist.",
            status.HTTP_404_NOT_FOUND,
        )


# --------------------------------------------------------------------------
# market data
# --------------------------------------------------------------------------


@router.get("/quote/{ticker}", response_model=QuoteResponse, tags=["market"])
def get_quote(ticker: str, provider: ProviderDep) -> QuoteResponse:
    quote = provider.get_quote(ticker)
    return QuoteResponse(
        ticker=quote.ticker,
        price=quote.price,
        currency=quote.currency,
        as_of=quote.as_of,
        previous_close=quote.previous_close,
    )


@router.get("/chain/{ticker}", response_model=ChainResponse, tags=["market"])
def get_chain(
    ticker: str,
    provider: ProviderDep,
    settings: SettingsDep,
    expiry: Annotated[
        date | None, Query(description="Defaults to the nearest listed expiry.")
    ] = None,
    right: Annotated[str | None, Query(pattern="^(call|put)$")] = None,
    near_the_money: Annotated[
        float | None,
        Query(gt=0, le=100, description="Keep strikes within this percent of spot."),
    ] = None,
) -> ChainResponse:
    """Full chain with locally computed greeks and implied volatility."""
    rate = resolve_risk_free_rate(settings)
    expiries = provider.get_expiries(ticker)
    resolved = provider.resolve_expiry(ticker, expiry)

    chain = provider.get_chain(ticker, resolved)
    frame = build_chain_frame(chain, rate, settings.options_dividend_yield, chain.as_of)

    if right:
        frame = frame[frame["right"] == right]
    if near_the_money is not None:
        frame = frame[(frame["moneyness"] - 1.0).abs() <= near_the_money / 100.0]

    summary = summarise_chain(frame)
    contracts = [
        ContractOut.model_validate(
            {
                str(key): _none_if_na(value)
                for key, value in record.items()
                if key in _CONTRACT_FIELDS
            }
        )
        for record in frame.to_dict("records")
    ]

    return ChainResponse(
        ticker=chain.ticker,
        expiry=resolved,
        spot=chain.spot,
        as_of=chain.as_of,
        provider=provider.name,
        risk_free_rate=rate,
        dividend_yield=settings.options_dividend_yield,
        time_to_expiry=float(frame.attrs["time_to_expiry"]),
        available_expiries=list(expiries),
        summary=ChainSummary(
            contracts=summary.contracts,
            solved=summary.solved,
            solve_rate=summary.solve_rate,
            atm_iv=summary.atm_iv,
            total_volume=summary.total_volume,
            total_open_interest=summary.total_open_interest,
            unsolved_reasons=summary.unsolved_reasons,
        ),
        contracts=contracts,
    )


_CONTRACT_FIELDS = set(ContractOut.model_fields)


def _none_if_na(value: Any) -> Any:
    """pandas NA/NaN are not JSON; Pydantic must see a real None."""
    import pandas as pd  # noqa: PLC0415

    if value is None:
        return None
    return None if not isinstance(value, list | dict) and bool(pd.isna(value)) else value


# --------------------------------------------------------------------------
# analytics over stored history
# --------------------------------------------------------------------------


@router.get("/ivrank/{ticker}", response_model=IVRankResponse, tags=["analytics"])
def get_iv_rank(ticker: str, session: SessionDep, settings: SettingsDep) -> IVRankResponse:
    """IV rank from locally accumulated history.

    Returns 200 with `rank: null` and a reason when history is insufficient. This
    is not an error -- it is the correct answer on day three, and a client that
    got a 404 here would have nothing useful to render.
    """
    result = iv_rank_for(
        session, ticker, settings.options_iv_window_days, settings.options_min_history_days
    )
    return IVRankResponse(
        ticker=ticker.upper(),
        rank=result.rank,
        percentile=result.percentile,
        current_iv=result.current_iv,
        iv_min=result.iv_min,
        iv_max=result.iv_max,
        iv_mean=result.iv_mean,
        days_available=result.days_available,
        days_required=result.days_required,
        window_days=result.window_days,
        status=result.status.value,
        reason=result.reason,
        first_observed=result.first_observed,
        last_observed=result.last_observed,
    )


@router.post(
    "/snapshot/{ticker}",
    response_model=SnapshotResponse,
    tags=["analytics"],
    summary="Capture today's chains (idempotent)",
)
def post_snapshot(
    ticker: str,
    session: SessionDep,
    provider: ProviderDep,
    settings: SettingsDep,
    expiries: Annotated[int, Query(ge=1, le=20)] = 6,
) -> SnapshotResponse:
    """Run the snapshot for one ticker. Re-running the same day refreshes it."""
    rate = resolve_risk_free_rate(settings)
    result = snapshot_ticker(
        session, provider, ticker, rate, settings.options_dividend_yield, expiries
    )
    return SnapshotResponse(
        ticker=result.ticker,
        snapshot_date=result.snapshot_date,
        contracts_written=result.contracts_written,
        contracts_solved=result.contracts_solved,
        solve_rate=result.solve_rate,
        expiries=result.expiries,
        atm_iv_30d=result.atm_iv_30d,
        created=result.created,
        errors=result.errors,
    )


@router.get("/screen", response_model=ScreenResponse, tags=["analytics"])
def get_screen(
    session: SessionDep,
    settings: SettingsDep,
    high: Annotated[float, Query(ge=0, le=100)] = 70.0,
    low: Annotated[float, Query(ge=0, le=100)] = 30.0,
    multiple: Annotated[float, Query(gt=1)] = 2.0,
) -> ScreenResponse:
    """Flag watchlist symbols where something has changed.

    Runs entirely on stored history, so it never touches the vendor and cannot
    be throttled. Purely descriptive — it narrows a list, it does not rank
    opportunities or suggest positions.
    """
    inputs = screen_inputs(
        session, settings.options_iv_window_days, settings.options_min_history_days
    )
    hits = screen(inputs, high, low, multiple)

    notes = [f"{item.ticker}: {item.iv_rank_reason}" for item in inputs if item.iv_rank is None]

    return ScreenResponse(
        screened=len(inputs),
        hits=[
            ScreenHitOut(
                ticker=hit.ticker,
                flags=[flag.value for flag in hit.flags],
                summary=hit.summary,
                iv_rank=hit.iv_rank,
                current_iv=hit.current_iv,
                volume_today=hit.volume_today,
                volume_median=hit.volume_median,
                volume_multiple=hit.volume_multiple,
                open_interest_today=hit.open_interest_today,
                open_interest_median=hit.open_interest_median,
                days_to_earnings=hit.days_to_earnings,
            )
            for hit in hits
        ],
        notes=notes,
        thresholds={"high_iv_rank": high, "low_iv_rank": low, "activity_multiple": multiple},
    )


@router.get("/earnings/{ticker}", response_model=EarningsResponse, tags=["market"])
def get_earnings(ticker: str, provider: ProviderDep) -> EarningsResponse:
    """Next scheduled earnings date, when the source has one.

    Returns 200 with `known: false` rather than a 404 when the date is unknown.
    No free source publishes reliable forward earnings dates for every symbol,
    and "we do not know" is a different answer from "none is scheduled".
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    when = provider.get_next_earnings_date(ticker)
    return EarningsResponse(
        ticker=ticker.upper(),
        next_earnings=when,
        days_away=(when - datetime.now(UTC).date()).days if when else None,
        known=when is not None,
    )


# --------------------------------------------------------------------------
# payoff
# --------------------------------------------------------------------------


@router.post("/payoff", response_model=PayoffResponse, tags=["analytics"])
def post_payoff(
    request: PayoffRequest, provider: ProviderDep, settings: SettingsDep
) -> PayoffResponse:
    """P/L for a multi-leg position, at expiry and as of today."""
    rate = resolve_risk_free_rate(settings)
    spot = request.spot
    chain_frame = None

    # Only touch the vendor if we actually need something from it.
    needs_lookup = spot is None or any(
        leg.volatility is None and leg.kind != "stock" for leg in request.legs
    )
    if needs_lookup and request.ticker:
        expiry = provider.resolve_expiry(request.ticker, request.expiry)
        chain = provider.get_chain(request.ticker, expiry)
        spot = spot or chain.spot
        chain_frame = build_chain_frame(chain, rate, settings.options_dividend_yield, chain.as_of)

    if spot is None:
        raise APIError(
            "spot_required",
            "Provide `spot`, or a `ticker` the service can look one up for.",
            status.HTTP_400_BAD_REQUEST,
        )

    time_to_expiry = _resolve_time_to_expiry(request, chain_frame)
    legs = [_to_leg(leg, chain_frame) for leg in request.legs]

    curve = build_payoff_curve(
        legs,
        spot,
        time_to_expiry,
        rate,
        settings.options_dividend_yield,
        request.price_range,
        request.points,
    )

    return PayoffResponse(
        underlying_prices=curve.underlying_prices,
        pnl_at_expiry=curve.pnl_at_expiry,
        pnl_today=curve.pnl_today,
        today_unavailable_reason=curve.today_unavailable_reason,
        net_cost=curve.net_cost,
        breakevens=curve.breakevens,
        max_profit=curve.max_profit,
        max_loss=curve.max_loss,
        unlimited_profit=curve.unlimited_profit,
        unlimited_loss=curve.unlimited_loss,
        spot=spot,
        time_to_expiry=curve.time_to_expiry,
    )


def _resolve_time_to_expiry(request: PayoffRequest, chain_frame: object) -> float:
    if request.time_to_expiry is not None:
        return request.time_to_expiry
    if request.expiry is not None:
        from options_tool.analytics.chain import time_to_expiry  # noqa: PLC0415

        return time_to_expiry(request.expiry)
    if chain_frame is not None:
        return float(chain_frame.attrs["time_to_expiry"])  # type: ignore[attr-defined]
    # No horizon given and none discoverable: the expiry curve is still exact,
    # and the today curve degrades to it rather than being fabricated.
    return 0.0


def _to_leg(leg: LegIn, chain_frame: object) -> Leg:
    """Convert a request leg, solving its volatility from the chain if omitted."""
    kind = LegKind(leg.kind)
    volatility = leg.volatility
    if volatility is None and kind is not LegKind.STOCK and chain_frame is not None:
        volatility = _lookup_iv(chain_frame, kind, leg.strike)
    return Leg(
        kind=kind,
        quantity=leg.quantity,
        premium=leg.premium,
        strike=leg.strike,
        volatility=volatility,
    )


def _lookup_iv(frame: object, kind: LegKind, strike: float | None) -> float | None:
    """Implied vol of the nearest solved strike on the right side of the chain."""
    if strike is None:
        return None
    subset = frame[(frame["right"] == kind.value) & frame["iv"].notna()]  # type: ignore[index]
    if subset.empty:
        return None
    nearest = subset.iloc[(subset["strike"] - strike).abs().argmin()]
    return float(nearest["iv"])
