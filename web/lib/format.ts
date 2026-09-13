/**
 * Display formatting.
 *
 * The rule running through this file: **a missing value renders as an em dash,
 * never as 0.00 or 0.0%.** Every formatter takes `number | null | undefined` and
 * returns a placeholder for the missing case, so no component has to remember
 * to guard — and none can accidentally print a zero that looks like a reading.
 */

export const MISSING = "—";

export function money(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function signedMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return `${value >= 0 ? "+" : "−"}${money(Math.abs(value))}`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return `${(value * 100).toFixed(digits)}%`;
}

export function number(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return value.toFixed(digits);
}

export function integer(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return Math.round(value).toLocaleString("en-US");
}

/** Theta is quoted per calendar day, not per year. */
export function thetaPerDay(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return (value / 365).toFixed(3);
}

/** Vega is quoted per volatility point, not per 1.00 of vol. */
export function vegaPerPoint(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return MISSING;
  return (value / 100).toFixed(3);
}

export function days(years: number | null | undefined): string {
  if (years == null || !Number.isFinite(years)) return MISSING;
  const value = years * 365;
  return value < 1 ? `${(value * 24).toFixed(0)}h` : `${Math.round(value)}d`;
}

export function shortDate(value: string | null | undefined): string {
  if (!value) return MISSING;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * Plain-language label for why a contract has no implied volatility.
 *
 * Shown in place of the number, so the table never has an unexplained blank.
 */
export function ivStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    no_quote: "no quote",
    non_positive_price: "no bid",
    below_intrinsic: "stale quote",
    above_max: "crossed",
    expired: "expired",
    no_bracket: "unsolvable",
    not_converged: "no solution",
    invalid_input: "bad data",
  };
  return labels[status] ?? status.replace(/_/g, " ");
}
