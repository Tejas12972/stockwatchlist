"use client";

/**
 * Watchlist.
 *
 * The `stored_days` column is the important one: it shows how much IV history
 * each symbol has accumulated, which is what determines whether its IV rank is
 * available at all.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, type WatchlistItem } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { EmptyState, ErrorState, Skeleton } from "@/components/states";

const MIN_HISTORY_DAYS = 20;

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await api.watchlist());
    } catch (cause) {
      setError(cause);
      setItems(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = symbol.trim().toUpperCase();
    if (!trimmed || busy) return;

    setBusy(true);
    setError(null);
    try {
      await api.addToWatchlist(trimmed);
      setSymbol("");
      await load();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function remove(target: string) {
    setBusy(true);
    try {
      await api.removeFromWatchlist(target);
      await load();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function snapshot(target: string) {
    setBusy(true);
    setError(null);
    try {
      await api.snapshot(target);
      await load();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">
          Watchlist
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Each snapshot adds one day of implied-volatility history. IV rank
          becomes available at {MIN_HISTORY_DAYS} days.
        </p>
      </div>

      <form onSubmit={add} className="flex gap-2">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Add a ticker, e.g. SPY"
          aria-label="Ticker symbol"
          maxLength={16}
          className="w-56 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm uppercase placeholder:normal-case placeholder:text-slate-600 focus:border-sky-600 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || symbol.trim() === ""}
          className="rounded-md bg-sky-700 px-4 py-2 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-40"
        >
          Add
        </button>
      </form>

      {error != null && <ErrorState error={error} onRetry={load} />}

      {items === null && !error && <Skeleton rows={4} />}

      {items !== null && items.length === 0 && (
        <EmptyState title="No symbols tracked yet">
          Add one above, then take a snapshot to start building its volatility
          history.
        </EmptyState>
      )}

      {items !== null && items.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900/60 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="px-4 py-2 text-left">Symbol</th>
                <th scope="col" className="px-4 py-2 text-right">Stored days</th>
                <th scope="col" className="px-4 py-2 text-left">History</th>
                <th scope="col" className="px-4 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.symbol} className="border-t border-slate-800">
                  <th scope="row" className="px-4 py-3 text-left font-medium">
                    <Link
                      href={`/chain/${item.symbol}`}
                      className="text-sky-400 hover:text-sky-300"
                    >
                      {item.symbol}
                    </Link>
                  </th>
                  <td className="px-4 py-3 text-right tnum">
                    <span
                      className={
                        item.stored_days >= MIN_HISTORY_DAYS
                          ? "text-slate-200"
                          : "text-slate-500"
                      }
                    >
                      {item.stored_days}
                    </span>
                    <span className="text-slate-600">/{MIN_HISTORY_DAYS}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {item.stored_days === 0
                      ? "no snapshots yet"
                      : `${shortDate(item.first_snapshot)} — ${shortDate(item.last_snapshot)}`}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => void snapshot(item.symbol)}
                      disabled={busy}
                      className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                    >
                      Snapshot
                    </button>
                    <button
                      type="button"
                      onClick={() => void remove(item.symbol)}
                      disabled={busy}
                      className="ml-2 rounded border border-slate-800 px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-800 disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-slate-500">
        Removing a symbol stops the daily job fetching it but keeps its stored
        history — those observations cannot be re-downloaded from anywhere.
      </p>
    </div>
  );
}
