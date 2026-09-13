"use client";

/**
 * Payoff diagram: P/L at expiry and, where it can be computed, P/L today.
 *
 * The today-curve is only drawn when the server could price every leg. When a
 * leg has no implied volatility it is **omitted with a stated reason** rather
 * than drawn at an assumed vol — on a chart the two look identical, and there
 * would be nothing to tell the reader which they were looking at.
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PayoffResponse } from "@/lib/api";
import { money, signedMoney } from "@/lib/format";

export function PayoffChart({ data }: { data: PayoffResponse }) {
  const points = data.underlying_prices.map((price, i) => ({
    price,
    expiry: data.pnl_at_expiry[i],
    today: data.pnl_today?.[i] ?? null,
  }));

  return (
    <div className="space-y-4">
      <div className="h-80 w-full rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 12, bottom: 8, left: 8 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis
              dataKey="price"
              tickFormatter={(value: number) => value.toFixed(0)}
              stroke="#64748b"
              fontSize={12}
              label={{
                value: "Underlying at expiry",
                position: "insideBottom",
                offset: -4,
                fill: "#64748b",
                fontSize: 12,
              }}
            />
            <YAxis
              tickFormatter={(value: number) => `${value >= 0 ? "" : "−"}${Math.abs(value).toFixed(0)}`}
              stroke="#64748b"
              fontSize={12}
              width={64}
            />
            <Tooltip
              contentStyle={{
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: 6,
                fontSize: 12,
              }}
              labelFormatter={(value: number) => `Underlying ${money(value)}`}
              // Recharts types the tooltip value as a loose ValueType union, so
              // it is narrowed here rather than asserted away.
              formatter={(value, name) => [
                typeof value === "number" ? signedMoney(value) : "—",
                String(name),
              ]}
            />
            <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine y={0} stroke="#475569" />
            {data.spot != null && (
              <ReferenceLine
                x={data.spot}
                stroke="#64748b"
                strokeDasharray="4 4"
                label={{ value: "spot", fill: "#64748b", fontSize: 11, position: "top" }}
              />
            )}
            {data.breakevens.map((breakeven) => (
              <ReferenceLine
                key={breakeven}
                x={breakeven}
                stroke="#0ea5e9"
                strokeDasharray="2 4"
                strokeOpacity={0.7}
              />
            ))}
            <Line
              type="monotone"
              dataKey="expiry"
              name="At expiry"
              stroke="#38bdf8"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            {data.pnl_today && (
              <Line
                type="monotone"
                dataKey="today"
                name="Today"
                stroke="#a78bfa"
                strokeWidth={2}
                strokeDasharray="5 4"
                dot={false}
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {data.today_unavailable_reason && (
        <p className="rounded border border-amber-900/60 bg-amber-950/20 px-3 py-2 text-xs text-amber-200/80">
          Today&rsquo;s curve is not shown: {data.today_unavailable_reason}
        </p>
      )}

      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat
          label={data.net_cost >= 0 ? "Net debit" : "Net credit"}
          value={money(Math.abs(data.net_cost))}
        />
        <Stat
          label="Max profit"
          value={data.unlimited_profit ? "Unlimited" : money(data.max_profit)}
          tone={data.unlimited_profit ? "text-emerald-300" : undefined}
        />
        <Stat
          label="Max loss"
          value={data.unlimited_loss ? "Unlimited" : money(data.max_loss)}
          tone={data.unlimited_loss ? "text-red-300" : undefined}
        />
        <Stat
          label={data.breakevens.length === 1 ? "Breakeven" : "Breakevens"}
          value={
            data.breakevens.length === 0
              ? "None"
              : data.breakevens.map((b) => b.toFixed(2)).join(" / ")
          }
        />
      </dl>

      {(data.unlimited_loss || data.unlimited_profit) && (
        <p className="text-xs text-slate-500">
          &ldquo;Unlimited&rdquo; is reported from the position&rsquo;s exact
          slope beyond the outermost strike, not from where this chart happens to
          stop. Only the upside can be unbounded — the underlying can rise
          without limit but cannot fall below zero.
        </p>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`mt-1 text-lg font-semibold tnum ${tone ?? "text-slate-100"}`}>
        {value}
      </dd>
    </div>
  );
}
