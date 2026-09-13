/**
 * Client-side payoff preview.
 *
 * This is a **second implementation** of maths that already exists in
 * `api/options_tool/analytics/payoff.py`, and that duplication is deliberate: it
 * lets the spread builder redraw instantly as you drag a strike, without a round
 * trip per keystroke. The API remains the source of truth — what is rendered on
 * submit comes from the server.
 *
 * Two implementations of the same formulas is exactly the setup where they
 * silently drift apart, so both are tested against the *same* golden file,
 * `api/tests/fixtures/payoff_golden.json`. If these ever disagree with the
 * Python engine, `lib/payoff.test.ts` goes red.
 *
 * Only the expiry curve lives here. The today-curve needs Black-Scholes and a
 * volatility per leg, which the server solves from the live chain — reproducing
 * that in the browser would be duplicating the part that actually matters.
 */

export const OPTION_MULTIPLIER = 100;

export type LegKind = "call" | "put" | "stock";

export interface Leg {
  kind: LegKind;
  /** Signed: positive is long, negative is short. */
  quantity: number;
  /** Paid or received per share; always positive. */
  premium: number;
  strike?: number | null;
  volatility?: number | null;
}

export interface PayoffShape {
  netCost: number;
  breakevens: number[];
  /** `null` means genuinely unlimited, never a large placeholder number. */
  maxProfit: number | null;
  maxLoss: number | null;
  upsideSlope: number;
  downsideSlope: number;
}

export interface PayoffCurve extends PayoffShape {
  underlyingPrices: number[];
  pnlAtExpiry: number[];
}

export class LegError extends Error {}

/** Shares (or share-equivalents) one unit of this leg controls. */
export function multiplier(leg: Leg): number {
  return leg.kind === "stock" ? 1 : OPTION_MULTIPLIER;
}

/** Signed cash flow to open. Positive is a debit paid, negative a credit. */
export function legCost(leg: Leg): number {
  return leg.quantity * leg.premium * multiplier(leg);
}

export function validateLeg(leg: Leg): void {
  if (!Number.isFinite(leg.quantity) || leg.quantity === 0) {
    throw new LegError("Leg quantity must be a non-zero whole number.");
  }
  if (!Number.isFinite(leg.premium) || leg.premium < 0) {
    throw new LegError(
      "Premium is quoted positive — use a negative quantity for a short leg.",
    );
  }
  if (leg.kind === "stock") {
    if (leg.strike != null) throw new LegError("A stock leg has no strike.");
  } else if (leg.strike == null || leg.strike <= 0) {
    throw new LegError(`A ${leg.kind} leg needs a positive strike.`);
  }
}

/** Value of one share of this leg at expiry. */
function intrinsic(leg: Leg, underlying: number): number {
  if (leg.kind === "stock") return underlying;
  const strike = leg.strike as number;
  return leg.kind === "call"
    ? Math.max(underlying - strike, 0)
    : Math.max(strike - underlying, 0);
}

/** Position P/L at expiry for a given settlement price. */
export function payoffAtExpiry(legs: Leg[], underlying: number): number {
  let value = 0;
  let cost = 0;
  for (const leg of legs) {
    value += leg.quantity * intrinsic(leg, underlying) * multiplier(leg);
    cost += legCost(leg);
  }
  return value - cost;
}

/**
 * dP/dS above every strike, and below every strike.
 *
 * Above all strikes each call is in the money and each put is worthless; below
 * all strikes the reverse. Stock is linear throughout.
 */
function terminalSlopes(legs: Leg[]): { upside: number; downside: number } {
  let upside = 0;
  let downside = 0;
  for (const leg of legs) {
    const weight = leg.quantity * multiplier(leg);
    if (leg.kind === "stock") {
      upside += weight;
      downside += weight;
    } else if (leg.kind === "call") {
      upside += weight;
    } else {
      downside -= weight;
    }
  }
  return { upside, downside };
}

/**
 * Prices where the expiry payoff changes slope, plus both ends.
 *
 * Zero is included because the underlying can reach it. That bound is why a long
 * put's profit is large but finite while a short call's loss is not.
 */
function kinkPrices(legs: Leg[]): number[] {
  const strikes = [
    ...new Set(
      legs
        .map((leg) => leg.strike)
        .filter((strike): strike is number => strike != null),
    ),
  ].sort((a, b) => a - b);
  const beyond = strikes.length > 0 ? (strikes.at(-1) as number) * 2 : 1;
  return [0, ...strikes, beyond];
}

/**
 * Exact extremes and breakevens, derived from the payoff's shape rather than
 * scanned off the plotting grid — so they do not change with the point count,
 * and a breakeven beyond the last strike is still found.
 */
export function analysePayoff(legs: Leg[]): PayoffShape {
  if (legs.length === 0) throw new LegError("A position needs at least one leg.");
  legs.forEach(validateLeg);

  const { upside, downside } = terminalSlopes(legs);
  const kinks = kinkPrices(legs);
  const values = kinks.map((price) => payoffAtExpiry(legs, price));

  const breakevens: number[] = [];
  for (let i = 0; i < kinks.length - 1; i += 1) {
    const lo = values[i] as number;
    const hi = values[i + 1] as number;
    if (lo === 0) {
      breakevens.push(kinks[i] as number);
    } else if (lo * hi < 0) {
      const weight = Math.abs(lo) / (Math.abs(lo) + Math.abs(hi));
      breakevens.push(
        (kinks[i] as number) +
          weight * ((kinks[i + 1] as number) - (kinks[i] as number)),
      );
    }
  }

  const lastKink = kinks.at(-1) as number;
  const lastValue = values.at(-1) as number;
  if (upside !== 0) {
    const crossing = lastKink - lastValue / upside;
    if (crossing > lastKink) breakevens.push(crossing);
  } else if (lastValue === 0) {
    breakevens.push(lastKink);
  }

  return {
    netCost: legs.reduce((total, leg) => total + legCost(leg), 0),
    breakevens: [...new Set(breakevens)].sort((a, b) => a - b),
    // Only the upside can be unbounded: the underlying can rise without limit
    // but cannot fall below zero.
    maxProfit: upside > 0 ? null : Math.max(...values),
    maxLoss: upside < 0 ? null : Math.min(...values),
    upsideSlope: upside,
    downsideSlope: downside,
  };
}

export function buildPayoffCurve(
  legs: Leg[],
  spot: number,
  priceRange?: [number, number],
  points = 201,
): PayoffCurve {
  if (!Number.isFinite(spot) || spot <= 0) {
    throw new LegError("Spot must be a positive number.");
  }
  if (points < 3) throw new LegError("Need at least 3 points to draw a curve.");

  const shape = analysePayoff(legs);
  const [low, high] = priceRange ?? defaultRange(legs, spot);
  const step = (high - low) / (points - 1);

  const underlyingPrices = Array.from(
    { length: points },
    (_, i) => low + i * step,
  );

  return {
    ...shape,
    underlyingPrices,
    pnlAtExpiry: underlyingPrices.map((price) => payoffAtExpiry(legs, price)),
  };
}

function defaultRange(legs: Leg[], spot: number): [number, number] {
  const strikes = legs
    .map((leg) => leg.strike)
    .filter((strike): strike is number => strike != null);
  const low = Math.min(spot, ...strikes) * 0.7;
  const high = Math.max(spot, ...strikes) * 1.3;
  return [Math.max(low, 0.01), high];
}

/** Human label for a position, e.g. "Long 1 105C / Short 2 110C". */
export function describePosition(legs: Leg[]): string {
  return legs
    .map((leg) => {
      const side = leg.quantity > 0 ? "Long" : "Short";
      const size = Math.abs(leg.quantity);
      if (leg.kind === "stock") return `${side} ${size} shares`;
      const right = leg.kind === "call" ? "C" : "P";
      return `${side} ${size} ${leg.strike}${right}`;
    })
    .join(" / ");
}
