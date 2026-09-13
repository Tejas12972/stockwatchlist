"use client";

/**
 * Spread builder.
 *
 * Editing a leg redraws immediately from `lib/payoff.ts` (the local preview),
 * while the server is asked for the authoritative curve — including the
 * today-line, which needs Black-Scholes and a volatility per leg that only the
 * API can solve from the live chain. Both implementations are pinned to the same
 * golden fixture, so the instant preview cannot disagree with what arrives.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, type PayoffResponse } from "@/lib/api";
import {
  analysePayoff,
  describePosition,
  LegError,
  type Leg,
  type LegKind,
} from "@/lib/payoff";
import { money } from "@/lib/format";
import { PayoffChart } from "@/components/PayoffChart";
import { ErrorState } from "@/components/states";

// Presets carry a volatility per leg so the today-curve is available on first
// load. Without one the expiry curve still draws, but the more interesting line
// -- what the position is worth if you close it now -- would be missing, and a
// new user would see the degraded state before ever seeing the working one.
const PRESETS: Record<string, Leg[]> = {
  "Bull call spread": [
    { kind: "call", quantity: 1, premium: 5, strike: 100, volatility: 0.25 },
    { kind: "call", quantity: -1, premium: 1.5, strike: 110, volatility: 0.23 },
  ],
  "Iron condor": [
    { kind: "put", quantity: 1, premium: 0.5, strike: 90, volatility: 0.3 },
    { kind: "put", quantity: -1, premium: 1.5, strike: 95, volatility: 0.27 },
    { kind: "call", quantity: -1, premium: 1.6, strike: 105, volatility: 0.24 },
    { kind: "call", quantity: 1, premium: 0.6, strike: 110, volatility: 0.23 },
  ],
  "Covered call": [
    { kind: "stock", quantity: 100, premium: 100 },
    { kind: "call", quantity: -1, premium: 3, strike: 105, volatility: 0.24 },
  ],
  "Long straddle": [
    { kind: "call", quantity: 1, premium: 5, strike: 100, volatility: 0.25 },
    { kind: "put", quantity: 1, premium: 4.5, strike: 100, volatility: 0.25 },
  ],
};

export default function PayoffPage() {
  const [legs, setLegs] = useState<Leg[]>(PRESETS["Bull call spread"] as Leg[]);
  const [spot, setSpot] = useState(100);
  const [timeToExpiry, setTimeToExpiry] = useState(0.25);
  const [server, setServer] = useState<PayoffResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  // Instant local shape, so dragging a strike does not wait on the network.
  const preview = useMemo(() => {
    try {
      return { shape: analysePayoff(legs), error: null as string | null };
    } catch (cause) {
      return {
        shape: null,
        error: cause instanceof LegError ? cause.message : "Invalid position.",
      };
    }
  }, [legs]);

  const fetchCurve = useCallback(async () => {
    if (preview.shape == null) return;
    setPending(true);
    setError(null);
    try {
      setServer(
        await api.payoff({
          spot,
          time_to_expiry: timeToExpiry,
          points: 161,
          legs: legs.map((leg) => ({
            kind: leg.kind,
            quantity: leg.quantity,
            premium: leg.premium,
            strike: leg.kind === "stock" ? null : leg.strike,
            volatility: leg.volatility ?? null,
          })),
        }),
      );
    } catch (cause) {
      setError(cause);
      setServer(null);
    } finally {
      setPending(false);
    }
  }, [legs, spot, timeToExpiry, preview.shape]);

  useEffect(() => {
    const timer = setTimeout(() => void fetchCurve(), 300);
    return () => clearTimeout(timer);
  }, [fetchCurve]);

  function update(index: number, patch: Partial<Leg>) {
    setLegs((current) =>
      current.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)),
    );
  }

  function changeKind(index: number, kind: LegKind) {
    update(index, { kind, strike: kind === "stock" ? null : (legs[index]?.strike ?? 100) });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">
          Spread builder
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {describePosition(legs) || "Add a leg to begin."}
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {Object.keys(PRESETS).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setLegs(PRESETS[name] as Leg[])}
            className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
          >
            {name}
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[26rem_1fr]">
        <div className="space-y-4">
          <div className="flex gap-3">
            <NumberField label="Spot" value={spot} onChange={setSpot} step={1} />
            <NumberField
              label="Years to expiry"
              value={timeToExpiry}
              onChange={setTimeToExpiry}
              step={0.05}
            />
          </div>

          <div className="space-y-2">
            {legs.map((leg, index) => (
              <div
                key={index}
                className="rounded-lg border border-slate-800 bg-slate-900/40 p-3"
              >
                <div className="flex items-center gap-2">
                  <select
                    value={leg.kind}
                    onChange={(e) => changeKind(index, e.target.value as LegKind)}
                    aria-label={`Leg ${index + 1} kind`}
                    className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
                  >
                    <option value="call">Call</option>
                    <option value="put">Put</option>
                    <option value="stock">Stock</option>
                  </select>
                  <button
                    type="button"
                    onClick={() =>
                      setLegs((current) => current.filter((_, i) => i !== index))
                    }
                    className="ml-auto text-xs text-slate-500 hover:text-red-300"
                  >
                    Remove
                  </button>
                </div>

                <div className="mt-2 grid grid-cols-3 gap-2">
                  <NumberField
                    label="Qty (± )"
                    value={leg.quantity}
                    onChange={(value) => update(index, { quantity: Math.round(value) })}
                    step={1}
                  />
                  {leg.kind !== "stock" && (
                    <NumberField
                      label="Strike"
                      value={leg.strike ?? 0}
                      onChange={(value) => update(index, { strike: value })}
                      step={1}
                    />
                  )}
                  <NumberField
                    label="Premium"
                    value={leg.premium}
                    onChange={(value) => update(index, { premium: value })}
                    step={0.05}
                  />
                </div>
                <p className="mt-1 text-[11px] text-slate-600">
                  Negative quantity is a short leg. Premium is always positive.
                </p>
              </div>
            ))}
          </div>

          <button
            type="button"
            onClick={() =>
              setLegs((current) => [
                ...current,
                {
                  kind: "call",
                  quantity: 1,
                  premium: 1,
                  strike: Math.round(spot),
                  volatility: 0.25,
                },
              ])
            }
            disabled={legs.length >= 8}
            className="w-full rounded-md border border-dashed border-slate-700 py-2 text-sm text-slate-400 hover:bg-slate-900 disabled:opacity-40"
          >
            Add leg
          </button>

          {preview.error && (
            <p className="rounded border border-amber-900/60 bg-amber-950/20 px-3 py-2 text-xs text-amber-200/80">
              {preview.error}
            </p>
          )}

          {preview.shape && (
            <p className="text-xs text-slate-500">
              Local preview:{" "}
              {preview.shape.netCost >= 0 ? "net debit" : "net credit"}{" "}
              {money(Math.abs(preview.shape.netCost))}
              {pending && " · fetching the priced curve…"}
            </p>
          )}
        </div>

        <div>
          {error != null && <ErrorState error={error} onRetry={() => void fetchCurve()} />}
          {server ? (
            <PayoffChart data={server} />
          ) : (
            !error && (
              <div className="flex h-80 items-center justify-center rounded-lg border border-dashed border-slate-800 text-sm text-slate-600">
                {preview.error ? "Fix the position to see a curve." : "Pricing…"}
              </div>
            )
          )}
        </div>
      </div>

      <p className="text-xs text-slate-500">
        Leave a leg&rsquo;s volatility unset and the server solves it from the
        live chain when a ticker is supplied. Where no volatility can be
        established, the today-curve is omitted rather than drawn at an assumed
        one — the expiry curve needs no volatility and is always exact.
      </p>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  step: number;
}) {
  return (
    <label className="block text-xs text-slate-500">
      {label}
      <input
        type="number"
        value={value}
        step={step}
        onChange={(e) => {
          const parsed = Number(e.target.value);
          if (Number.isFinite(parsed)) onChange(parsed);
        }}
        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm tnum text-slate-200 focus:border-sky-600 focus:outline-none"
      />
    </label>
  );
}
