"use client";

/**
 * Chain view for one ticker: IV rank alongside the full chain.
 *
 * The two are shown together deliberately — the chain tells you what volatility
 * is being priced right now, and IV rank (when there is enough history) tells
 * you whether that is high or low for this name.
 */

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { api, type ChainResponse, type IVRankResponse } from "@/lib/api";
import { days, percent, shortDate } from "@/lib/format";
import { ChainTable } from "@/components/ChainTable";
import { IVRankCard } from "@/components/IVRankCard";
import { ErrorState, Skeleton } from "@/components/states";

export default function ChainPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = use(params);
  const symbol = ticker.toUpperCase();

  const [chain, setChain] = useState<ChainResponse | null>(null);
  const [ivRank, setIvRank] = useState<IVRankResponse | null>(null);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (targetExpiry?: string) => {
      setLoading(true);
      setError(null);
      try {
        // IV rank reads only stored history, so it must not fail just because
        // the vendor is throttling the live chain.
        const [chainResult, rankResult] = await Promise.allSettled([
          api.chain(symbol, targetExpiry ? { expiry: targetExpiry } : undefined),
          api.ivRank(symbol),
        ]);

        if (chainResult.status === "fulfilled") {
          setChain(chainResult.value);
          setExpiry(chainResult.value.expiry);
        } else {
          setError(chainResult.reason);
        }

        setIvRank(rankResult.status === "fulfilled" ? rankResult.value : null);
      } finally {
        setLoading(false);
      }
    },
    [symbol],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">
          {symbol}
        </h1>
        {chain && (
          <>
            <span className="text-lg tnum text-slate-300">
              {chain.spot.toFixed(2)}
            </span>
            <span className="text-xs text-slate-500">
              as of {shortDate(chain.as_of)} · via {chain.provider}
            </span>
          </>
        )}
        <Link
          href="/payoff"
          className="ml-auto text-sm text-sky-400 hover:text-sky-300"
        >
          Build a spread →
        </Link>
      </div>

      {error != null && <ErrorState error={error} onRetry={() => void load()} />}

      {chain && (
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-400">
            Expiry
            <select
              value={expiry ?? chain.expiry}
              onChange={(e) => {
                setExpiry(e.target.value);
                void load(e.target.value);
              }}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
            >
              {chain.available_expiries.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <span className="text-xs text-slate-500">
            {days(chain.time_to_expiry)} to expiry · r ={" "}
            {percent(chain.risk_free_rate, 2)} · q ={" "}
            {percent(chain.dividend_yield, 2)}
          </span>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
        <div className="space-y-4">
          {ivRank ? (
            <IVRankCard data={ivRank} />
          ) : loading ? (
            <Skeleton rows={3} />
          ) : (
            <p className="rounded-lg border border-slate-800 p-4 text-sm text-slate-500">
              IV rank is unavailable — no stored history for {symbol} yet. Take a
              snapshot from the watchlist to begin accumulating it.
            </p>
          )}

          {chain && (
            <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-4 text-sm">
              <h2 className="text-sm font-medium text-slate-400">This chain</h2>
              <dl className="mt-2 space-y-1">
                <Row
                  label="Strikes solved"
                  value={`${chain.summary.solved} / ${chain.summary.contracts}`}
                />
                <Row label="ATM IV" value={percent(chain.summary.atm_iv)} />
                <Row
                  label="Open interest"
                  value={chain.summary.total_open_interest.toLocaleString("en-US")}
                />
              </dl>
              {Object.keys(chain.summary.unsolved_reasons).length > 0 && (
                <p className="mt-3 text-xs text-slate-500">
                  Unsolved:{" "}
                  {Object.entries(chain.summary.unsolved_reasons)
                    .map(([reason, count]) => `${count} ${reason.replace(/_/g, " ")}`)
                    .join(", ")}
                  .
                </p>
              )}
            </section>
          )}
        </div>

        <div>
          {loading && !chain ? (
            <Skeleton rows={10} />
          ) : chain ? (
            <ChainTable contracts={chain.contracts} spot={chain.spot} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-slate-500">{label}</dt>
      <dd className="tnum text-slate-200">{value}</dd>
    </div>
  );
}
