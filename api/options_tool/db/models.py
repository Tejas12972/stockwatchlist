"""SQLAlchemy schema for the locally-accumulated options history.

Three tables:

- ``tickers``   -- the watchlist
- ``snapshots`` -- one row per (ticker, day): the chain-level facts, including
                   the ATM implied volatility that IV rank is computed from
- ``contracts`` -- one row per listed contract in that snapshot

Idempotency is a **database guarantee, not application politeness.** The unique
constraint on ``(ticker_id, snapshot_date)`` combined with the one on
``(snapshot_id, expiry, strike, right)`` makes the pair
(ticker, date, expiry, strike, right) unique by construction, so re-running the
snapshot job cannot duplicate rows even if the writer has a bug (SPEC.md
section 3).

``snapshots.atm_iv_30d`` is deliberately computed at write time rather than
derived on read. IV rank is a query over a daily series; storing the series
means that query is an index scan over a few hundred rows rather than a
re-solve of every contract ever captured.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = ["Base", "Ticker", "Snapshot", "Contract"]


class Base(DeclarativeBase):
    pass


class Ticker(Base):
    """A symbol being tracked. `active` is what makes this the watchlist."""

    __tablename__ = "tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    snapshots: Mapped[list[Snapshot]] = relationship(
        back_populates="ticker", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Ticker {self.symbol}{'' if self.active else ' (inactive)'}>"


class Snapshot(Base):
    """One capture of one ticker's chains on one day.

    The pricing assumptions in force at capture time (`risk_free_rate`,
    `dividend_yield`) are stored alongside the results. Without them a stored IV
    is uninterpretable later -- you could not tell a genuine volatility move from
    a change to the rate the tool happened to be configured with.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint("ticker_id", "snapshot_date", name="uq_snapshot_ticker_date"),
        Index("ix_snapshot_ticker_date", "ticker_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("tickers.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    spot: Mapped[float] = mapped_column(Float, nullable=False)
    risk_free_rate: Mapped[float] = mapped_column(Float, nullable=False)
    dividend_yield: Mapped[float] = mapped_column(Float, nullable=False)

    contract_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    solved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Nullable on purpose: a day where no ATM vol could be established stores
    # null and is excluded from IV rank, rather than contributing a zero that
    # would drag the historical minimum down forever.
    atm_iv_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    atm_iv_front: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    ticker: Mapped[Ticker] = relationship(back_populates="snapshots")
    contracts: Mapped[list[Contract]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def solve_rate(self) -> float:
        """Share of contracts whose IV solved. A low rate means distrust this day."""
        return self.solved_count / self.contract_count if self.contract_count else 0.0

    def __repr__(self) -> str:
        return f"<Snapshot {self.ticker_id} {self.snapshot_date} n={self.contract_count}>"


class Contract(Base):
    """One listed option contract as captured, with our own computed analytics.

    `iv` and the greeks are ours. There is no column for a vendor-supplied greek
    and none should be added -- see SPEC.md section 4.
    """

    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "expiry", "strike", "right", name="uq_contract_snapshot_key"
        ),
        Index("ix_contract_expiry", "snapshot_id", "expiry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False
    )

    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    right: Mapped[str] = mapped_column(String(4), nullable=False)
    contract_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- as quoted by the vendor ---
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- what we priced off, and what we derived ---
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    time_to_expiry: Mapped[float] = mapped_column(Float, nullable=False)
    moneyness: Mapped[float] = mapped_column(Float, nullable=False)

    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_status: Mapped[str] = mapped_column(String(24), nullable=False)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    vega: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    rho: Mapped[float | None] = mapped_column(Float, nullable=True)

    snapshot: Mapped[Snapshot] = relationship(back_populates="contracts")

    def __repr__(self) -> str:
        return f"<Contract {self.expiry} {self.strike}{self.right[0].upper()}>"
