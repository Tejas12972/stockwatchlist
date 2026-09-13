"""Resolve the risk-free rate used by Black-Scholes.

A constant by default, because a constant you can read in `.env.example` is
honest and reproducible. Optionally the live 13-week T-bill (^IRX), which is a
better short-dated proxy but makes every run non-deterministic -- so it is
opt-in, and a failed fetch falls back to the constant with a warning rather than
aborting the run or silently using zero.
"""

from __future__ import annotations

import logging

from options_tool.config import Settings

__all__ = ["resolve_risk_free_rate", "IRX_SYMBOL"]

logger = logging.getLogger(__name__)

IRX_SYMBOL = "^IRX"
"""13-week US Treasury bill discount rate, quoted by Yahoo in percent."""


def resolve_risk_free_rate(settings: Settings) -> float:
    """The annualised rate to price with, per the configured policy."""
    if not settings.options_fetch_risk_free_rate:
        return settings.options_risk_free_rate

    try:
        import yfinance  # noqa: PLC0415 -- keeps the network out of module import

        value = yfinance.Ticker(IRX_SYMBOL).fast_info.get("lastPrice")
        rate = float(value) / 100.0
    except Exception as exc:  # noqa: BLE001 -- any failure here is non-fatal by design
        logger.warning(
            "could not fetch %s, falling back to the configured rate %.4f: %s",
            IRX_SYMBOL,
            settings.options_risk_free_rate,
            exc,
        )
        return settings.options_risk_free_rate

    # A T-bill outside this band means the quote is garbage, not that rates moved.
    if not -0.01 <= rate <= 0.25:
        logger.warning(
            "%s returned an implausible rate (%.4f); using the configured %.4f instead",
            IRX_SYMBOL,
            rate,
            settings.options_risk_free_rate,
        )
        return settings.options_risk_free_rate

    logger.info("using live %s risk-free rate: %.4f", IRX_SYMBOL, rate)
    return rate
