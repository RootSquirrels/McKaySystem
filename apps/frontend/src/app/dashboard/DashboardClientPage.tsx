"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo } from "react";

import { useAuth } from "@/hooks/useAuth";
import { useInitialValueKpis } from "@/hooks/useInitialValueKpis";
import { ApiError } from "@/lib/api/client";
import { formatUtcDateTime } from "@/lib/dates";
import { getStoredScope } from "@/lib/scope";

function formatMoney(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(1)}%`;
}

function kpisErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code})` : "";
    return `Failed to load KPI dashboard [${error.status}${code}]: ${error.message}`;
  }
  if (error instanceof Error) {
    return `Failed to load KPI dashboard: ${error.message}`;
  }
  return "Failed to load KPI dashboard.";
}

function coverageTone(status: string | null): string {
  const normalized = (status ?? "").trim().toLowerCase();
  if (normalized === "healthy") {
    return "border-emerald-200 bg-emerald-50/80";
  }
  if (normalized === "degraded") {
    return "border-amber-200 bg-amber-50/80";
  }
  return "border-rose-200 bg-rose-50/80";
}

function panelTone(value: "cyan" | "emerald" | "amber" | "violet"): string {
  if (value === "emerald") {
    return "border-emerald-200 bg-emerald-50/80";
  }
  if (value === "amber") {
    return "border-amber-200 bg-amber-50/80";
  }
  if (value === "violet") {
    return "border-violet-200 bg-violet-50/80";
  }
  return "border-cyan-200 bg-cyan-50/80";
}

export function DashboardClientPage() {
  const router = useRouter();
  const scope = getStoredScope();
  const auth = useAuth();
  const permissions = useMemo(() => new Set(auth.user?.permissions ?? []), [auth.user?.permissions]);
  const canReadFindings = permissions.has("admin:full") || permissions.has("findings:read");
  const canReadRuns = permissions.has("admin:full") || permissions.has("runs:read");
  const canReadUsers = permissions.has("admin:full") || permissions.has("users:read");
  const isAdminFull = permissions.has("admin:full");

  const dashboard = useInitialValueKpis(canReadFindings);

  useEffect(() => {
    if (!scope) {
      router.replace("/login");
      return;
    }
    if (!auth.isLoading && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.isAuthenticated, auth.isLoading, router, scope]);

  if (!scope) {
    return null;
  }

  const kpis = dashboard.data?.kpis;
  const latestRun = dashboard.data?.latest_run;
  const trend = dashboard.data?.trend;

  return (
    <main className="finops-shell relative overflow-hidden">
      <div className="finops-orb finops-orb--one" />
      <div className="finops-orb finops-orb--two" />
      <div className="finops-orb finops-orb--three" />
      <div className="relative z-10 mx-auto min-h-screen w-full max-w-7xl px-6 py-6">
        <section className="finops-panel rounded-3xl p-6">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.3em] text-cyan-700">Initial Value Reporting</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">Platform KPI Dashboard</h1>
              <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
                This page keeps findings, recommendations, realized savings, and coverage as separate KPI families so
                we can prove value without mixing incompatible signals.
              </p>
              <div className="mt-4 flex flex-wrap gap-3 text-xs text-slate-500">
                <span className="rounded-full border border-slate-200 bg-white/70 px-3 py-1">
                  Scope: {scope.tenantId} / {scope.workspace}
                </span>
                <span className="rounded-full border border-slate-200 bg-white/70 px-3 py-1">
                  Latest run: {latestRun?.run_id ?? "-"}
                </span>
                <span className="rounded-full border border-slate-200 bg-white/70 px-3 py-1">
                  As of: {latestRun?.run_ts ? formatUtcDateTime(latestRun.run_ts) : "-"}
                </span>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Link className="rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-white/80" href="/findings">
                Findings
              </Link>
              <Link className="rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-white/80" href="/recommendations">
                Recommendations
              </Link>
              <Link className="rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-white/80" href="/remediations">
                Realized Savings
              </Link>
              <Link className="rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-white/80" href="/coverage">
                Coverage
              </Link>
              {canReadUsers ? (
                <Link className="rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-white/80" href="/users">
                  Users
                </Link>
              ) : null}
              {isAdminFull ? (
                <Link
                  className="rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-white/80"
                  href="/tenant-admin"
                >
                  Tenant Admin
                </Link>
              ) : null}
            </div>
          </div>
        </section>

        {dashboard.isLoading ? (
          <section className="mt-6 finops-panel rounded-2xl p-6 text-sm text-slate-600">Loading KPI dashboard...</section>
        ) : null}

        {dashboard.error ? (
          <section className="mt-6 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            {kpisErrorMessage(dashboard.error)}
          </section>
        ) : null}

        {kpis ? (
          <>
            <section className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <article className={`finops-panel rounded-2xl border p-4 ${panelTone("cyan")}`}>
                <p className="text-sm font-medium text-slate-600">Detected Waste</p>
                <p className="mt-2 text-3xl font-semibold text-slate-900">{kpis.findings.open_findings_count}</p>
                <p className="mt-2 text-sm text-slate-700">
                  Open finding-estimated savings: {formatMoney(kpis.findings.estimated_monthly_savings)}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Needs attention: {kpis.findings.needs_attention_count}
                  {trend ? ` | New vs previous run: +${trend.findings.new_count} / -${trend.findings.disappeared_count}` : ""}
                </p>
              </article>

              <article className={`finops-panel rounded-2xl border p-4 ${panelTone("violet")}`}>
                <p className="text-sm font-medium text-slate-600">Recommendation Ready</p>
                <p className="mt-2 text-3xl font-semibold text-slate-900">
                  {kpis.recommendations.eligible_recommendations_count}
                </p>
                <p className="mt-2 text-sm text-slate-700">
                  Eligible recommendation savings: {formatMoney(kpis.recommendations.estimated_monthly_savings)}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Priority P1: {kpis.recommendations.priority_p1_count}
                  {trend
                    ? ` | Delta vs previous run: ${trend.recommendations.eligible_count_delta >= 0 ? "+" : ""}${trend.recommendations.eligible_count_delta}`
                    : ""}
                </p>
              </article>

              <article className={`finops-panel rounded-2xl border p-4 ${panelTone("emerald")}`}>
                <p className="text-sm font-medium text-slate-600">Realized Savings</p>
                <p className="mt-2 text-3xl font-semibold text-slate-900">
                  {formatMoney(kpis.realized.realized_total_monthly_savings)}
                </p>
                <p className="mt-2 text-sm text-slate-700">
                  Realization rate: {formatPercent(kpis.realized.realization_rate_pct)}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Tracked actions: {kpis.realized.actions_count} | Not yet realized:{" "}
                  {formatMoney(kpis.realized.estimated_not_realized_monthly_savings)}
                </p>
              </article>

              <article className={`finops-panel rounded-2xl border p-4 ${coverageTone(kpis.coverage.coverage_status)}`}>
                <p className="text-sm font-medium text-slate-600">Coverage Health</p>
                <p className="mt-2 text-3xl font-semibold text-slate-900">
                  {canReadRuns ? formatPercent(kpis.coverage.coverage_pct) : "-"}
                </p>
                <p className="mt-2 text-sm text-slate-700">
                  Status: {kpis.coverage.coverage_status ?? "unknown"} | Confidence: {kpis.coverage.confidence ?? "-"}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Permission gaps: {kpis.coverage.permission_gap_count} | Failed assessments: {kpis.coverage.assessment_failed}
                  {trend?.coverage
                    ? ` | Delta: ${trend.coverage.coverage_pct_delta >= 0 ? "+" : ""}${trend.coverage.coverage_pct_delta.toFixed(2)} pts`
                    : ""}
                </p>
              </article>
            </section>

            <section className="mt-6 grid gap-4 lg:grid-cols-[1.3fr_1fr]">
              <article className="finops-panel rounded-2xl p-5">
                <h2 className="text-lg font-semibold text-slate-900">How To Read These KPIs</h2>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div className="rounded-2xl border border-slate-200 bg-white/70 p-4">
                    <p className="text-sm font-semibold text-slate-900">Findings</p>
                    <p className="mt-2 text-sm leading-6 text-slate-600">{kpis.findings.definition}</p>
                    <p className="mt-2 text-xs text-slate-500">Source: {kpis.findings.source}</p>
                  </div>
                  <div className="rounded-2xl border border-slate-200 bg-white/70 p-4">
                    <p className="text-sm font-semibold text-slate-900">Recommendations</p>
                    <p className="mt-2 text-sm leading-6 text-slate-600">{kpis.recommendations.definition}</p>
                    <p className="mt-2 text-xs text-slate-500">Source: {kpis.recommendations.source}</p>
                  </div>
                  <div className="rounded-2xl border border-slate-200 bg-white/70 p-4">
                    <p className="text-sm font-semibold text-slate-900">Realized</p>
                    <p className="mt-2 text-sm leading-6 text-slate-600">{kpis.realized.definition}</p>
                    <p className="mt-2 text-xs text-slate-500">Source: {kpis.realized.source}</p>
                  </div>
                  <div className="rounded-2xl border border-slate-200 bg-white/70 p-4">
                    <p className="text-sm font-semibold text-slate-900">Coverage</p>
                    <p className="mt-2 text-sm leading-6 text-slate-600">{kpis.coverage.definition}</p>
                    <p className="mt-2 text-xs text-slate-500">Source: {kpis.coverage.source}</p>
                  </div>
                </div>
              </article>

              <article className="finops-panel rounded-2xl p-5">
                <h2 className="text-lg font-semibold text-slate-900">Important Notes</h2>
                <div className="mt-4 space-y-3 text-sm leading-6 text-slate-600">
                  {dashboard.data?.notes.map((note) => (
                    <p key={note} className="rounded-2xl border border-slate-200 bg-white/70 p-3">
                      {note}
                    </p>
                  ))}
                  {trend ? (
                    <p className="rounded-2xl border border-slate-200 bg-white/70 p-3">
                      Trend deltas compare the latest two ready runs. They reflect run-to-run detection and coverage
                      movement, while the KPI cards themselves reflect current state.
                    </p>
                  ) : null}
                </div>
              </article>
            </section>
          </>
        ) : null}
      </div>
    </main>
  );
}
