"use client";

/**
 * The option chain.
 *
 * Rows whose implied volatility did not solve are **kept and labelled**, not
 * dropped: a strike the model cannot price is information, and silently removing
 * it would make the chain look healthier than it is. The header carries the
 * solve rate for the same reason.
 */

import { useMemo, useState } from "react";

import type { Contract } from "@/lib/api";
import {
  integer,
  ivStatusLabel,
  money,
  number,
  percent,
  thetaPerDay,
  vegaPerPoint,
} from "@/lib/format";

type SortKey = "strike" | "iv" | "delta" | "volume" | "open_interest";

export function ChainTable({
  contracts,
  spot,
}: {
  contracts: Contract[];
  spot: number;
}) {
  const [right, setRight] = useState<"call" | "put">("call");
  const [sortKey, setSortKey] = useState<SortKey>("strike");
  const [nearOnly, setNearOnly] = useState(true);

  const rows = useMemo(() => {
    let filtered = contracts.filter((c) => c.right === right);
    if (nearOnly) {
      filtered = filtered.filter((c) => Math.abs(c.moneyness - 1) <= 0.15);
    }
    return [...filtered].sort((a, b) => {
      if (sortKey === "strike") return a.strike - b.strike;
      // Unsolved rows sort last rather than being treated as zero.
      const left = a[sortKey] ?? -Infinity;
      const rightValue = b[sortKey] ?? -Infinity;
      return rightValue - left;
    });
  }, [contracts, right, sortKey, nearOnly]);

  const unsolved = rows.filter((r) => r.iv == null).length;

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/40">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-800 px-4 py-3">
        <div className="flex rounded-md border border-slate-700 p-0.5">
          {(["call", "put"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setRight(option)}
              className={`rounded px-3 py-1 text-xs font-medium capitalize ${
                right === option
                  ? "bg-slate-700 text-slate-100"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {option}s
            </button>
          ))}
        </div>

        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={nearOnly}
            onChange={(e) => setNearOnly(e.target.checked)}
            className="accent-sky-600"
          />
          Near the money only
        </label>

        <label className="flex items-center gap-2 text-xs text-slate-400">
          Sort
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
          >
            <option value="strike">Strike</option>
            <option value="iv">Implied vol</option>
            <option value="delta">Delta</option>
            <option value="volume">Volume</option>
            <option value="open_interest">Open interest</option>
          </select>
        </label>

        <span className="ml-auto text-xs text-slate-500">
          {rows.length - unsolved}/{rows.length} strikes solved
        </span>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full text-right text-sm tnum">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-800">
              <th scope="col" className="px-3 py-2 text-left">Strike</th>
              <th scope="col" className="px-3 py-2">Bid</th>
              <th scope="col" className="px-3 py-2">Ask</th>
              <th scope="col" className="px-3 py-2">IV</th>
              <th scope="col" className="px-3 py-2">Delta</th>
              <th scope="col" className="px-3 py-2">Gamma</th>
              <th scope="col" className="px-3 py-2" title="Per volatility point">
                Vega
              </th>
              <th scope="col" className="px-3 py-2" title="Per calendar day">
                Theta
              </th>
              <th scope="col" className="px-3 py-2">Vol</th>
              <th scope="col" className="px-3 py-2">OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((contract) => {
              const solved = contract.iv != null;
              const atTheMoney = Math.abs(contract.strike - spot) < 2.5;
              return (
                <tr
                  key={`${contract.right}-${contract.strike}`}
                  className={`border-b border-slate-800/60 ${
                    solved ? "" : "text-slate-600"
                  } ${atTheMoney ? "bg-slate-800/30" : ""}`}
                >
                  <th
                    scope="row"
                    className={`px-3 py-1.5 text-left font-normal ${
                      contract.in_the_money ? "text-slate-100" : "text-slate-400"
                    }`}
                  >
                    {number(contract.strike)}
                  </th>
                  <td className="px-3 py-1.5">{money(contract.bid)}</td>
                  <td className="px-3 py-1.5">{money(contract.ask)}</td>
                  <td className="px-3 py-1.5">
                    {solved ? (
                      percent(contract.iv)
                    ) : (
                      <span
                        className="text-xs italic text-slate-600"
                        title={`No implied volatility: ${contract.iv_status}`}
                      >
                        {ivStatusLabel(contract.iv_status)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">{number(contract.delta, 4)}</td>
                  <td className="px-3 py-1.5">{number(contract.gamma, 5)}</td>
                  <td className="px-3 py-1.5">{vegaPerPoint(contract.vega)}</td>
                  <td className="px-3 py-1.5">{thetaPerDay(contract.theta)}</td>
                  <td className="px-3 py-1.5">{integer(contract.volume)}</td>
                  <td className="px-3 py-1.5">{integer(contract.open_interest)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <footer className="border-t border-slate-800 px-4 py-2 text-xs text-slate-500">
        Vega is per volatility point; theta is per calendar day. Greyed rows had
        no solvable implied volatility — the reason is shown in place of the
        number, and no greek is estimated for them.
      </footer>
    </section>
  );
}
