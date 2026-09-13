/**
 * Typed client for the analytics API.
 *
 * Two things this file insists on:
 *
 * 1. **Nullable fields stay nullable.** `iv`, `delta` and `rank` are
 *    `number | null`, and nothing here coalesces a null to zero. A zero IV rank
 *    reads as "volatility is at its lows"; a null one means "we do not know
 *    yet", and the UI must be able to tell them apart.
 * 2. **Errors are values, not thrown strings.** The API returns a typed body
 *    with a machine-readable `code`; `ApiError` carries it through so a
 *    component can distinguish a throttled vendor from a bad ticker.
 */

/**
 * Same-origin. Requests go to this app's own `/api/[...path]` route, which
 * forwards them to the analytics API server-side.
 *
 * This used to be `process.env.NEXT_PUBLIC_API_BASE_URL`, compiled into the
 * client bundle at build time — which meant the image only worked against the
 * host it was built for, and moving the API required a rebuild. Proxying makes
 * the upstream address runtime configuration (`API_INTERNAL_URL`, read by the
 * route handler) and lets the API stay off the public internet entirely.
 */
export const API_BASE_URL = "/api";

export interface ErrorBody {
  code: string;
  message: string;
  detail?: Record<string, unknown> | null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail?: Record<string, unknown> | null;

  constructor(body: ErrorBody, status: number) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.status = status;
    this.detail = body.detail;
  }

  /** Whether retrying the same request could plausibly succeed. */
  get retryable(): boolean {
    return this.code === "provider_rate_limited" || this.status >= 500;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    // This now means the *proxy route* is unreachable, which in practice means
    // the Next.js server itself is down — the upstream API failing is returned
    // by the route handler as a normal 502/504 with a typed body, not as a
    // thrown fetch error. "Failed to fetch" tells a user nothing actionable, so
    // the original cause is deliberately replaced.
    throw new ApiError(
      {
        code: "api_unreachable",
        message:
          "Could not reach the application server. If you are running locally, check that `npm run dev` is still running.",
      },
      0,
    );
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    if (body && typeof body === "object" && "code" in body) {
      throw new ApiError(body as ErrorBody, response.status);
    }
    // FastAPI's own 422 validation errors have a different shape.
    throw new ApiError(
      {
        code: "invalid_request",
        message:
          response.status === 422
            ? "The request was rejected as invalid."
            : `Request failed with status ${response.status}.`,
        detail: body as Record<string, unknown> | null,
      },
      response.status,
    );
  }

  return body as T;
}

// --- responses -------------------------------------------------------------

export interface WatchlistItem {
  symbol: string;
  active: boolean;
  note: string | null;
  stored_days: number;
  first_snapshot: string | null;
  last_snapshot: string | null;
}

export interface Contract {
  expiry: string;
  strike: number;
  right: "call" | "put";
  bid: number | null;
  ask: number | null;
  last: number | null;
  mid: number | null;
  volume: number | null;
  open_interest: number | null;
  price: number | null;
  price_source: "mid" | "last" | null;
  time_to_expiry: number;
  moneyness: number;
  in_the_money: boolean;
  iv: number | null;
  iv_status: string;
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
  rho: number | null;
}

export interface ChainSummary {
  contracts: number;
  solved: number;
  solve_rate: number;
  atm_iv: number | null;
  total_volume: number;
  total_open_interest: number;
  unsolved_reasons: Record<string, number>;
}

export interface ChainResponse {
  ticker: string;
  expiry: string;
  spot: number;
  as_of: string;
  provider: string;
  risk_free_rate: number;
  dividend_yield: number;
  time_to_expiry: number;
  available_expiries: string[];
  summary: ChainSummary;
  contracts: Contract[];
  disclaimer: string;
}

export interface IVRankResponse {
  ticker: string;
  rank: number | null;
  percentile: number | null;
  current_iv: number | null;
  iv_min: number | null;
  iv_max: number | null;
  iv_mean: number | null;
  days_available: number;
  days_required: number;
  window_days: number;
  status: string;
  reason: string;
  first_observed: string | null;
  last_observed: string | null;
}

export interface PayoffResponse {
  underlying_prices: number[];
  pnl_at_expiry: number[];
  pnl_today: number[] | null;
  today_unavailable_reason: string | null;
  net_cost: number;
  breakevens: number[];
  max_profit: number | null;
  max_loss: number | null;
  unlimited_profit: boolean;
  unlimited_loss: boolean;
  spot: number | null;
  time_to_expiry: number;
}

export interface SnapshotResponse {
  ticker: string;
  snapshot_date: string;
  contracts_written: number;
  contracts_solved: number;
  solve_rate: number;
  expiries: string[];
  atm_iv_30d: number | null;
  created: boolean;
  errors: string[];
}

export interface HealthResponse {
  status: string;
  version: string;
  provider: string;
  database_reachable: boolean;
}

// --- calls -----------------------------------------------------------------

export const api = {
  health: () => request<HealthResponse>("/health"),

  watchlist: () =>
    request<{ items: WatchlistItem[] }>("/watchlist").then((r) => r.items),

  addToWatchlist: (symbol: string) =>
    request<WatchlistItem>("/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }),

  removeFromWatchlist: (symbol: string) =>
    request<void>(`/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),

  chain: (ticker: string, options?: { expiry?: string; nearTheMoney?: number }) => {
    const params = new URLSearchParams();
    if (options?.expiry) params.set("expiry", options.expiry);
    if (options?.nearTheMoney != null) {
      params.set("near_the_money", String(options.nearTheMoney));
    }
    const query = params.toString();
    return request<ChainResponse>(
      `/chain/${encodeURIComponent(ticker)}${query ? `?${query}` : ""}`,
    );
  },

  ivRank: (ticker: string) =>
    request<IVRankResponse>(`/ivrank/${encodeURIComponent(ticker)}`),

  snapshot: (ticker: string) =>
    request<SnapshotResponse>(`/snapshot/${encodeURIComponent(ticker)}`, {
      method: "POST",
    }),

  payoff: (payload: unknown) =>
    request<PayoffResponse>("/payoff", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
