/**
 * IV rank, including the state where there is no rank to show.
 *
 * This component is the visible half of the project's core design decision. IV
 * rank needs history the tool accumulates one day at a time, and before there is
 * enough of it there is no honest number to render. So the degraded state gets
 * the same visual weight as a real reading — a progress bar toward the required
 * window and an explicit day count — rather than a blank, a dash, or the zero
 * that a naive implementation would show and that reads as "volatility is at its
 * lows".
 */

import type { IVRankResponse } from "@/lib/api";
import { percent, shortDate } from "@/lib/format";

function rankTone(rank: number): string {
  if (rank >= 70) return "text-amber-300";
  if (rank <= 30) return "text-sky-300";
  return "text-slate-200";
}

export function IVRankCard({ data }: { data: IVRankResponse }) {
  if (data.rank == null) {
    return <UnavailableRank data={data} />;
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
      <header className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-slate-400">IV rank</h2>
        <span className="text-xs text-slate-500">
          {data.days_available} days · {data.window_days}-day window
        </span>
      </header>

      <p className={`mt-2 text-4xl font-semibold tnum ${rankTone(data.rank)}`}>
        {data.rank.toFixed(1)}
      </p>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
        <Row label="IV percentile" value={`${data.percentile?.toFixed(1)}`} />
        <Row label="Current ATM IV" value={percent(data.current_iv)} />
        <Row label="Window low" value={percent(data.iv_min)} />
        <Row label="Window high" value={percent(data.iv_max)} />
      </dl>

      <p className="mt-4 text-xs text-slate-500">
        Ranked against {data.days_available} stored days,{" "}
        {shortDate(data.first_observed)} to {shortDate(data.last_observed)}.
        Percentile is the share of those days below today, and is the more robust
        of the two — a single outlier sets the range that rank is measured
        against.
      </p>
    </section>
  );
}

function UnavailableRank({ data }: { data: IVRankResponse }) {
  const progress =
    data.days_required > 0
      ? Math.min(data.days_available / data.days_required, 1)
      : 0;
  const insufficient = data.status === "insufficient_history";

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
      <header className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-slate-400">IV rank</h2>
        <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-400">
          unavailable
        </span>
      </header>

      <p className="mt-2 text-2xl font-semibold text-slate-400">
        {insufficient
          ? `${data.days_available} / ${data.days_required} days`
          : "No rank"}
      </p>
      <p className="mt-1 text-sm text-slate-400">{data.reason}</p>

      {insufficient && (
        <div
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-800"
          role="progressbar"
          aria-valuenow={data.days_available}
          aria-valuemin={0}
          aria-valuemax={data.days_required}
          aria-label="Stored history toward the minimum window"
        >
          <div
            className="h-full rounded-full bg-sky-700"
            style={{ width: `${progress * 100}%` }}
          />
        </div>
      )}

      {data.current_iv != null && (
        <p className="mt-4 text-sm text-slate-300">
          Today&rsquo;s at-the-money IV is{" "}
          <span className="font-medium tnum">{percent(data.current_iv)}</span>.
          That much is known — what is missing is the history to rank it against.
        </p>
      )}

      <p className="mt-4 text-xs text-slate-500">
        Historical implied volatility is not available from free data sources, so
        this tool builds its own: one snapshot per day, accumulating from the
        first run. Until there are {data.days_required} days, any rank would be
        an artefact of too few points rather than a measurement.
      </p>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right tnum text-slate-200">{value}</dd>
    </>
  );
}
