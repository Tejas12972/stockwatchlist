/**
 * Loading, empty and error states.
 *
 * Every async surface in this app uses these, because the failure modes here are
 * specific and worth naming: the API might not be running, the vendor might be
 * throttling (retryable), or a ticker might simply not exist (not retryable).
 * A spinner that never resolves tells the user none of that.
 */

import { ApiError } from "@/lib/api";

export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="animate-pulse space-y-2" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-9 rounded bg-slate-800/60" />
      ))}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const apiError = error instanceof ApiError ? error : null;
  const message =
    apiError?.message ??
    (error instanceof Error ? error.message : "Something went wrong.");

  // Only offer a retry where retrying could actually help. A button that
  // re-runs a request guaranteed to fail the same way is worse than none.
  const canRetry = onRetry && (apiError?.retryable ?? true);

  return (
    <div
      role="alert"
      className="rounded-lg border border-red-900/60 bg-red-950/30 p-4 text-sm"
    >
      <p className="font-medium text-red-300">{message}</p>
      {apiError?.code === "provider_rate_limited" && (
        <p className="mt-2 text-red-200/70">
          This is normal for the free data source. It usually clears within a
          minute.
        </p>
      )}
      {apiError?.code === "expiry_not_found" &&
        Array.isArray(apiError.detail?.available_expiries) && (
          <p className="mt-2 text-red-200/70">
            Available expiries:{" "}
            {(apiError.detail.available_expiries as string[]).join(", ")}
          </p>
        )}
      {canRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-red-800 px-3 py-1.5 text-xs font-medium text-red-200 hover:bg-red-900/40"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center">
      <p className="font-medium text-slate-300">{title}</p>
      {children && <div className="mt-2 text-sm text-slate-500">{children}</div>}
    </div>
  );
}
