"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/hooks/useAuth";
import {
  RemediationImpactItem,
  RemediationImpactQualityRow,
  useRemediationImpact,
} from "@/hooks/useRemediationImpact";
import { ApiError } from "@/lib/api/client";
import { formatUtcDateTime } from "@/lib/dates";
import { getStoredScope } from "@/lib/scope";

function parsePositiveInt(value: string | null, fallback: number): number {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return fallback;
  }
  return parsed;
}

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

function remediationsErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code})` : "";
    return `Failed to load remediation impact [${error.status}${code}]: ${error.message}`;
  }
  if (error instanceof Error) {
    return `Failed to load remediation impact: ${error.message}`;
  }
  return "Failed to load remediation impact.";
}

function actionStatusBadgeClass(value: string): string {
  const key = value.trim().toLowerCase();
  if (key === "completed") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (key === "failed" || key === "cancelled" || key === "rejected") {
    return "border-rose-300 bg-rose-50 text-rose-800";
  }
  if (key === "approved" || key === "queued" || key === "running") {
    return "border-cyan-300 bg-cyan-50 text-cyan-800";
  }
  return "border-amber-300 bg-amber-50 text-amber-800";
}

function outcomeBadgeClass(value: string): string {
  const key = value.trim().toLowerCase();
  if (key === "realized_full") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (key === "realized_partial") {
    return "border-sky-300 bg-sky-50 text-sky-800";
  }
  if (key === "pending_verification") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-rose-300 bg-rose-50 text-rose-800";
}

function summaryCardTone(value: "cyan" | "emerald" | "amber" | "rose"): string {
  if (value === "emerald") {
    return "border-emerald-200 bg-emerald-50/80";
  }
  if (value === "amber") {
    return "border-amber-200 bg-amber-50/80";
  }
  if (value === "rose") {
    return "border-rose-200 bg-rose-50/80";
  }
  return "border-cyan-200 bg-cyan-50/80";
}

function qualityBandClass(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return "border-zinc-300 bg-zinc-100 text-zinc-700";
  }
  if (value >= 80) {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (value >= 50) {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-rose-300 bg-rose-50 text-rose-800";
}

export function RemediationsImpactClientPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const scope = getStoredScope();
  const auth = useAuth();
  const actionStatusFilter = searchParams.get("action_status") ?? "";
  const verificationStatusFilter = searchParams.get("verification_status") ?? "";
  const actionTypeFilter = searchParams.get("action_type") ?? "";
  const checkIdFilter = searchParams.get("check_id") ?? "";
  const limit = parsePositiveInt(searchParams.get("limit"), 50);
  const page = parsePositiveInt(searchParams.get("page"), 1);
  const offset = (page - 1) * limit;
  const [actionTypeInput, setActionTypeInput] = useState(actionTypeFilter);
  const [checkIdInput, setCheckIdInput] = useState(checkIdFilter);
  const permissions = useMemo(() => new Set(auth.user?.permissions ?? []), [auth.user?.permissions]);
  const canReadFindings = permissions.has("admin:full") || permissions.has("findings:read");
  const canReadUsers = permissions.has("admin:full") || permissions.has("users:read");

  const impact = useRemediationImpact({
    limit,
    offset,
    actionStatus: actionStatusFilter,
    verificationStatus: verificationStatusFilter,
    actionType: actionTypeFilter,
    checkId: checkIdFilter,
  });

  useEffect(() => {
    setActionTypeInput(actionTypeFilter);
  }, [actionTypeFilter]);

  useEffect(() => {
    setCheckIdInput(checkIdFilter);
  }, [checkIdFilter]);

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

  const total = impact.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const items = impact.data?.items ?? [];
  const summary = impact.data?.summary;
  const quality = impact.data?.quality;

  function pushWithParams(updates: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (!value) {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    }
    const query = params.toString();
    router.push(query ? `/remediations?${query}` : "/remediations");
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    pushWithParams({
      action_type: actionTypeInput.trim() || null,
      check_id: checkIdInput.trim() || null,
      page: "1",
    });
  }

  function pageSummaryText(item: RemediationImpactItem): string {
    const actionType = item.action_type.trim() || "action";
    const checkId = item.check_id.trim() || "unknown";
    return `${actionType} | ${checkId}`;
  }

  function renderQualityTable(title: string, rows: RemediationImpactQualityRow[]) {
    return (
      <section className="finops-panel rounded-2xl p-4">
        <div className="mb-3">
          <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
          <p className="text-sm text-slate-600">
            Ranked by realized savings within the current remediation impact filter scope.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-y-2 text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2">Group</th>
                <th className="px-3 py-2">Actions</th>
                <th className="px-3 py-2">Realized</th>
                <th className="px-3 py-2">Not Realized</th>
                <th className="px-3 py-2">Savings Rate</th>
                <th className="px-3 py-2">Success Rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.group_key} className="rounded-2xl bg-white/80 shadow-sm ring-1 ring-slate-200/80">
                  <td className="px-3 py-3 align-top font-medium text-slate-900">{row.group_key}</td>
                  <td className="px-3 py-3 align-top text-slate-700">
                    <div>{row.actions_count}</div>
                    <div className="text-xs text-slate-500">
                      Full {row.fully_realized_count} | Partial {row.partial_realization_count}
                    </div>
                  </td>
                  <td className="px-3 py-3 align-top font-medium text-emerald-700">
                    {formatMoney(row.realized_total_monthly_savings)}
                  </td>
                  <td className="px-3 py-3 align-top font-medium text-amber-700">
                    {formatMoney(row.estimated_not_realized_monthly_savings)}
                  </td>
                  <td className="px-3 py-3 align-top">
                    <span
                      className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${qualityBandClass(row.realization_rate_pct)}`}
                    >
                      {formatPercent(row.realization_rate_pct)}
                    </span>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <span
                      className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${qualityBandClass(row.effective_success_rate_pct)}`}
                    >
                      {formatPercent(row.effective_success_rate_pct)}
                    </span>
                  </td>
                </tr>
              ))}
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-sm text-slate-500">
                    No quality rollups matched the current filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    );
  }

  return (
    <main className="finops-shell relative overflow-hidden">
      <div className="finops-orb finops-orb--one" />
      <div className="finops-orb finops-orb--two" />
      <div className="finops-orb finops-orb--three" />

      <div className="relative z-10 mx-auto min-h-screen w-full max-w-7xl px-6 py-6">
        <header className="finops-panel mb-4 flex flex-wrap items-start justify-between gap-3 rounded-2xl p-4">
          <div>
            <p className="inline-flex rounded-full border border-cyan-300/70 bg-cyan-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-700">
              Realized Savings
            </p>
            <h1 className="font-display mt-2 text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">
              Remediation Impact Loop
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Tenant: <span className="font-medium">{scope.tenantId}</span> | Workspace:{" "}
              <span className="font-medium">{scope.workspace}</span>
            </p>
            <p className="text-sm text-slate-600">
              Closed-loop view of predicted vs realized savings after remediation execution and post-run verification.
            </p>
          </div>
          <div className="flex items-center gap-2 self-start">
            {canReadFindings ? (
              <button
                type="button"
                className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
                onClick={() => {
                  router.push("/recommendations");
                }}
              >
                Recommendations
              </button>
            ) : null}
            {canReadFindings ? (
              <button
                type="button"
                className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
                onClick={() => {
                  router.push("/findings");
                }}
              >
                Findings
              </button>
            ) : null}
            {canReadUsers ? (
              <button
                type="button"
                className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
                onClick={() => {
                  router.push("/users");
                }}
              >
                Users
              </button>
            ) : null}
            <button
              type="button"
              className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700 transition hover:border-rose-400 hover:bg-rose-100"
              onClick={async () => {
                await auth.logout();
                router.push("/login");
              }}
            >
              Logout
            </button>
          </div>
        </header>

        <section className="mb-4 grid gap-3 md:grid-cols-4">
          <article className={`finops-panel rounded-2xl border p-4 ${summaryCardTone("cyan")}`}>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Tracked actions</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{summary?.actions_count ?? 0}</p>
            <p className="mt-1 text-sm text-slate-600">Actions with remediation impact snapshots.</p>
          </article>
          <article className={`finops-panel rounded-2xl border p-4 ${summaryCardTone("emerald")}`}>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Realized monthly</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">
              {formatMoney(summary?.realized_total_monthly_savings ?? null)}
            </p>
            <p className="mt-1 text-sm text-slate-600">
              Realization rate: {formatPercent(summary?.realization_rate_pct ?? null)}
            </p>
          </article>
          <article className={`finops-panel rounded-2xl border p-4 ${summaryCardTone("amber")}`}>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Not yet realized</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">
              {formatMoney(summary?.estimated_not_realized_monthly_savings ?? null)}
            </p>
            <p className="mt-1 text-sm text-slate-600">
              Partial: {summary?.partial_realization_count ?? 0} | Pending: {summary?.pending_count ?? 0}
            </p>
          </article>
          <article className={`finops-panel rounded-2xl border p-4 ${summaryCardTone("rose")}`}>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Needs attention</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">
              {(summary?.no_realization_count ?? 0) + (summary?.failed_count ?? 0)}
            </p>
            <p className="mt-1 text-sm text-slate-600">
              Not realized: {summary?.no_realization_count ?? 0} | Failed: {summary?.failed_count ?? 0}
            </p>
          </article>
        </section>

        <section className="finops-panel mb-4 rounded-2xl p-4 text-sm">
          <div className="grid gap-3 md:grid-cols-4">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Action status</span>
              <select
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={actionStatusFilter}
                onChange={(event) => {
                  pushWithParams({ action_status: event.target.value || null, page: "1" });
                }}
              >
                <option value="">All</option>
                <option value="pending_approval">Pending approval</option>
                <option value="approved">Approved</option>
                <option value="queued">Queued</option>
                <option value="running">Running</option>
                <option value="completed">Completed</option>
                <option value="failed">Failed</option>
                <option value="cancelled">Cancelled</option>
                <option value="rejected">Rejected</option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Outcome</span>
              <select
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={verificationStatusFilter}
                onChange={(event) => {
                  pushWithParams({ verification_status: event.target.value || null, page: "1" });
                }}
              >
                <option value="">All</option>
                <option value="verified_resolved">Verified resolved</option>
                <option value="verified_persistent">Verified persistent</option>
                <option value="pending_post_run">Pending verification</option>
                <option value="execution_failed">Execution failed</option>
              </select>
            </label>

            <form className="md:col-span-2" onSubmit={applyFilters}>
              <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Action type</span>
                  <input
                    value={actionTypeInput}
                    onChange={(event) => {
                      setActionTypeInput(event.target.value);
                    }}
                    placeholder="rightsize, terminate, tune..."
                    className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Check ID</span>
                  <input
                    value={checkIdInput}
                    onChange={(event) => {
                      setCheckIdInput(event.target.value);
                    }}
                    placeholder="aws.ec2.instances.underutilized"
                    className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                  />
                </label>
                <button
                  type="submit"
                  className="self-end rounded-lg border border-cyan-300 bg-cyan-50 px-4 py-2 font-medium text-cyan-800 transition hover:border-cyan-400 hover:bg-cyan-100"
                >
                  Apply
                </button>
              </div>
            </form>
          </div>
        </section>

        {impact.isError ? (
          <section className="finops-panel mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {remediationsErrorMessage(impact.error)}
          </section>
        ) : null}

        <section className="finops-panel rounded-2xl p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Impact review queue</h2>
              <p className="text-sm text-slate-600">
                {impact.isLoading
                  ? "Loading remediation outcomes..."
                  : `Showing ${items.length} action(s) out of ${total} tracked impact row(s).`}
              </p>
            </div>
            <div className="text-sm text-slate-600">
              Page {page} of {totalPages}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-y-2 text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Outcome</th>
                  <th className="px-3 py-2">Baseline</th>
                  <th className="px-3 py-2">Realized</th>
                  <th className="px-3 py-2">Not Realized</th>
                  <th className="px-3 py-2">Rate</th>
                  <th className="px-3 py-2">Verified Run</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.action_id} className="rounded-2xl bg-white/80 shadow-sm ring-1 ring-slate-200/80">
                    <td className="px-3 py-3 align-top">
                      <div className="font-medium text-slate-900">{pageSummaryText(item)}</div>
                      <div className="text-xs text-slate-500">Action ID: {item.action_id}</div>
                      <div className="text-xs text-slate-500">Fingerprint: {item.fingerprint}</div>
                    </td>
                    <td className="px-3 py-3 align-top">
                      <span
                        className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${actionStatusBadgeClass(item.action_status)}`}
                      >
                        {item.action_status}
                      </span>
                    </td>
                    <td className="px-3 py-3 align-top">
                      <div>
                        <span
                          className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${outcomeBadgeClass(item.outcome_status)}`}
                        >
                          {item.outcome_label}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-slate-500">{item.verification_status}</div>
                    </td>
                    <td className="px-3 py-3 align-top font-medium text-slate-900">
                      {formatMoney(item.baseline_estimated_monthly_savings)}
                    </td>
                    <td className="px-3 py-3 align-top font-medium text-emerald-700">
                      {formatMoney(item.realized_monthly_savings)}
                    </td>
                    <td className="px-3 py-3 align-top font-medium text-amber-700">
                      {formatMoney(item.estimated_not_realized_monthly_savings)}
                    </td>
                    <td className="px-3 py-3 align-top text-slate-700">
                      <div>{formatPercent(item.realization_rate_pct)}</div>
                      <div className="text-xs text-slate-500">Band: {item.realization_band}</div>
                    </td>
                    <td className="px-3 py-3 align-top text-slate-700">
                      <div>{item.latest_run_id ?? "-"}</div>
                      <div className="text-xs text-slate-500">
                        {formatUtcDateTime(item.latest_run_ts)}
                      </div>
                    </td>
                  </tr>
                ))}
                {!impact.isLoading && items.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-8 text-center text-sm text-slate-500">
                      No remediation impact rows matched the current filters.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex items-center justify-between">
            <button
              type="button"
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={page <= 1}
              onClick={() => {
                pushWithParams({ page: String(page - 1) });
              }}
            >
              Previous
            </button>
            <button
              type="button"
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={page >= totalPages}
              onClick={() => {
                pushWithParams({ page: String(page + 1) });
              }}
            >
              Next
            </button>
          </div>
        </section>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {renderQualityTable(
            "Recommendation Type Quality",
            quality?.by_recommendation_type ?? [],
          )}
          {renderQualityTable("Checker Quality", quality?.by_check_id ?? [])}
        </div>
      </div>
    </main>
  );
}
