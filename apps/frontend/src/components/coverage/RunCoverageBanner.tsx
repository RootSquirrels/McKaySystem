"use client";

import { RunCoverageLatestRun, RunCoverageSummary } from "@/hooks/useRunCoverageLatest";

function coverageTone(status: string | null): {
  panel: string;
  badge: string;
  accent: string;
} {
  switch ((status ?? "").trim().toLowerCase()) {
    case "healthy":
      return {
        panel: "border-emerald-200 bg-emerald-50/90",
        badge: "border-emerald-300 bg-emerald-100 text-emerald-800",
        accent: "text-emerald-900",
      };
    case "partial":
      return {
        panel: "border-amber-200 bg-amber-50/90",
        badge: "border-amber-300 bg-amber-100 text-amber-800",
        accent: "text-amber-900",
      };
    case "degraded":
    case "failed":
      return {
        panel: "border-rose-200 bg-rose-50/90",
        badge: "border-rose-300 bg-rose-100 text-rose-800",
        accent: "text-rose-900",
      };
    default:
      return {
        panel: "border-slate-200 bg-white/85",
        badge: "border-slate-300 bg-slate-100 text-slate-700",
        accent: "text-slate-900",
      };
  }
}

function formatStatus(value: string | null): string {
  const normalized = (value ?? "").trim();
  if (!normalized) {
    return "Unknown";
  }
  return normalized
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatCoveragePct(value: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(2)}%`;
}

function coverageMessage(summary: RunCoverageSummary, run: RunCoverageLatestRun | null): string {
  const permissionGaps = summary.permission_gap_count;
  const failed = summary.assessment_failed;
  const skipped = summary.skipped_total + summary.not_assessed_total;

  if (failed > 0) {
    return `${failed} assessment target${failed === 1 ? "" : "s"} failed in the latest run, so absence of findings does not always mean clean coverage.`;
  }
  if (permissionGaps > 0) {
    return `${permissionGaps} permission gap${permissionGaps === 1 ? "" : "s"} reduced visibility for the latest run${run?.run_id ? ` (${run.run_id})` : ""}.`;
  }
  if (skipped > 0) {
    return `${skipped} target${skipped === 1 ? "" : "s"} were skipped or not assessed, so the current results are only partially complete.`;
  }
  return "Latest run coverage looks healthy, so findings and recommendations were generated from a complete successful assessment set.";
}

interface RunCoverageBannerProps {
  run: RunCoverageLatestRun | null;
  summary: RunCoverageSummary | null;
}

/**
 * Summarize latest run coverage health for findings and recommendations views.
 */
export function RunCoverageBanner({ run, summary }: RunCoverageBannerProps) {
  if (!run || !summary) {
    return null;
  }

  const tone = coverageTone(summary.coverage_status ?? run.coverage_status ?? null);

  return (
    <section className={`mb-4 rounded-2xl border p-4 shadow-sm ${tone.panel}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className={`text-sm font-semibold ${tone.accent}`}>Coverage visibility</p>
          <p className="mt-1 text-sm text-slate-700">{coverageMessage(summary, run)}</p>
        </div>
        <span
          className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] ${tone.badge}`}
        >
          {formatStatus(summary.coverage_status ?? run.coverage_status ?? null)}
        </span>
      </div>

      <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-5">
        <div className="rounded-xl border border-white/60 bg-white/65 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Coverage</p>
          <p className="mt-1 font-semibold text-slate-900">{formatCoveragePct(summary.coverage_pct)}</p>
        </div>
        <div className="rounded-xl border border-white/60 bg-white/65 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Confidence</p>
          <p className="mt-1 font-semibold text-slate-900">{formatStatus(summary.confidence)}</p>
        </div>
        <div className="rounded-xl border border-white/60 bg-white/65 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Assessed</p>
          <p className="mt-1 font-semibold text-slate-900">
            {summary.assessed_total} / {summary.targets_total}
          </p>
        </div>
        <div className="rounded-xl border border-white/60 bg-white/65 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Failed</p>
          <p className="mt-1 font-semibold text-slate-900">{summary.assessment_failed}</p>
        </div>
        <div className="rounded-xl border border-white/60 bg-white/65 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Permission Gaps</p>
          <p className="mt-1 font-semibold text-slate-900">{summary.permission_gap_count}</p>
        </div>
      </div>
    </section>
  );
}
