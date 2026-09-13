"""Read helpers over the stored history.

Kept separate from the writer so the analytics stay ignorant of SQLAlchemy: this
module turns rows into the plain dataclasses `analytics/` already understands.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from options_tool.analytics.iv_rank import IVObservation, IVRankResult, compute_iv_rank
from options_tool.analytics.screener import ScreenInput
from options_tool.db.models import Contract, Snapshot, Ticker

__all__ = [
    "list_watchlist",
    "iv_observations",
    "iv_rank_for",
    "latest_snapshot",
    "history_depth",
    "add_to_watchlist",
    "remove_from_watchlist",
    "contract_count",
    "activity_history",
    "screen_inputs",
]


def list_watchlist(session: Session, include_inactive: bool = False) -> list[Ticker]:
    statement = select(Ticker).order_by(Ticker.symbol)
    if not include_inactive:
        statement = statement.where(Ticker.active.is_(True))
    return list(session.scalars(statement))


def add_to_watchlist(session: Session, symbol: str, note: str | None = None) -> Ticker:
    """Add a symbol, or reactivate it if it was previously removed.

    Reactivating rather than re-inserting keeps the ticker's accumulated
    snapshot history attached -- removing a symbol from the watchlist should not
    throw away months of IV observations you cannot get back.
    """
    normalised = symbol.strip().upper()
    ticker = session.scalar(select(Ticker).where(Ticker.symbol == normalised))
    if ticker is None:
        ticker = Ticker(symbol=normalised, active=True, note=note)
        session.add(ticker)
        session.flush()
        return ticker

    ticker.active = True
    if note is not None:
        ticker.note = note
    session.flush()
    return ticker


def remove_from_watchlist(session: Session, symbol: str) -> bool:
    """Deactivate a symbol. Returns False if it was not on the list.

    A soft delete: the history stays, the daily job stops fetching it.
    """
    ticker = session.scalar(select(Ticker).where(Ticker.symbol == symbol.strip().upper()))
    if ticker is None or not ticker.active:
        return False
    ticker.active = False
    session.flush()
    return True


def iv_observations(session: Session, symbol: str, limit: int | None = None) -> list[IVObservation]:
    """Stored daily ATM implied vols for a symbol, oldest first.

    Days whose `atm_iv_30d` is null are excluded rather than zero-filled -- a day
    where no ATM vol could be established is a missing observation, and zero
    would become a permanent artificial minimum in the IV-rank window.
    """
    statement = (
        select(Snapshot.snapshot_date, Snapshot.atm_iv_30d)
        .join(Ticker, Snapshot.ticker_id == Ticker.id)
        .where(Ticker.symbol == symbol.strip().upper(), Snapshot.atm_iv_30d.is_not(None))
        .order_by(Snapshot.snapshot_date.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)

    rows = list(session.execute(statement))
    return [
        IVObservation(observed_on=row.snapshot_date, iv=float(row.atm_iv_30d))
        for row in reversed(rows)
    ]


def iv_rank_for(
    session: Session, symbol: str, window_days: int = 252, min_history_days: int = 20
) -> IVRankResult:
    """IV rank for a symbol from stored history, honest about insufficiency."""
    observations = iv_observations(session, symbol, limit=window_days)
    return compute_iv_rank(observations, window_days, min_history_days)


def latest_snapshot(session: Session, symbol: str) -> Snapshot | None:
    return session.scalar(
        select(Snapshot)
        .join(Ticker, Snapshot.ticker_id == Ticker.id)
        .where(Ticker.symbol == symbol.strip().upper())
        .order_by(Snapshot.snapshot_date.desc())
        .limit(1)
    )


def history_depth(session: Session, symbol: str) -> tuple[int, date | None, date | None]:
    """(stored days, first date, last date) for a symbol. Cheap enough to call often."""
    row = session.execute(
        select(
            func.count(Snapshot.id),
            func.min(Snapshot.snapshot_date),
            func.max(Snapshot.snapshot_date),
        )
        .join(Ticker, Snapshot.ticker_id == Ticker.id)
        .where(Ticker.symbol == symbol.strip().upper())
    ).one()
    return int(row[0]), row[1], row[2]


def contract_count(session: Session, symbol: str | None = None) -> int:
    """Total stored contract rows, optionally for one symbol. Used to prove idempotency."""
    statement = select(func.count(Contract.id))
    if symbol is not None:
        statement = (
            statement.join(Snapshot, Contract.snapshot_id == Snapshot.id)
            .join(Ticker, Snapshot.ticker_id == Ticker.id)
            .where(Ticker.symbol == symbol.strip().upper())
        )
    return int(session.scalar(statement) or 0)


def activity_history(session: Session, symbol: str, limit: int = 60) -> tuple[list[int], list[int]]:
    """(volume, open interest) totals per stored day, oldest first.

    Aggregated in SQL rather than pulled row by row: a year of snapshots for one
    symbol is hundreds of thousands of contract rows, and the screener only ever
    wants two numbers a day from them.
    """
    rows = session.execute(
        select(
            Snapshot.snapshot_date,
            func.coalesce(func.sum(Contract.volume), 0),
            func.coalesce(func.sum(Contract.open_interest), 0),
        )
        .join(Contract, Contract.snapshot_id == Snapshot.id)
        .join(Ticker, Snapshot.ticker_id == Ticker.id)
        .where(Ticker.symbol == symbol.strip().upper())
        .group_by(Snapshot.snapshot_date)
        .order_by(Snapshot.snapshot_date.desc())
        .limit(limit)
    ).all()

    ordered = list(reversed(rows))
    return [int(row[1]) for row in ordered], [int(row[2]) for row in ordered]


def screen_inputs(
    session: Session,
    window_days: int = 252,
    min_history_days: int = 20,
    earnings: dict[str, int] | None = None,
    days_to_near_expiry: dict[str, int] | None = None,
) -> list[ScreenInput]:
    """Assemble what the screener needs for every active watchlist symbol.

    `earnings` and `days_to_near_expiry` are passed in rather than fetched here,
    because they need the live provider and this module deliberately touches
    only stored data.
    """
    inputs = []
    for ticker in list_watchlist(session):
        rank = iv_rank_for(session, ticker.symbol, window_days, min_history_days)
        volume, open_interest = activity_history(session, ticker.symbol)

        inputs.append(
            ScreenInput(
                ticker=ticker.symbol,
                iv_rank=rank.rank,
                iv_rank_reason=rank.reason,
                current_iv=rank.current_iv,
                # The last stored day is "today"; the rest is its own baseline,
                # so today is excluded from the median it is compared against.
                volume_today=volume[-1] if volume else None,
                volume_history=volume[:-1],
                open_interest_today=open_interest[-1] if open_interest else None,
                open_interest_history=open_interest[:-1],
                days_to_earnings=(earnings or {}).get(ticker.symbol),
                days_to_near_expiry=(days_to_near_expiry or {}).get(ticker.symbol),
            )
        )
    return inputs
