"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { RunCoverageBanner } from "@/components/coverage/RunCoverageBanner";
import { useAuth } from "@/hooks/useAuth";
import {
  CoverageCheckerItem,
  useRunCoverageAccounts,
  useRunCoverageCheckers,
  useRunCoverageHistory,
  useRunCoverageIssues,
  useRunCoverageRegressionLatest,
  useRunCoverageServices,
} from "@/hooks/useRunCoverageDetails";
import { useRunCoverageLatest } from "@/hooks/useRunCoverageLatest";
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

function formatDurationMs(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}

function apiErrorMessage(prefix: string, error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code})` : "";
    return `${prefix} [${error.status}${code}]: ${error.message}`;
  }
  if (error instanceof Error) {
    return `${prefix}: ${error.message}`;
  }
  return prefix;
}

function statusBadgeClass(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (normalized === "failed" || normalized === "assessment_failed") {
    return "border-rose-300 bg-rose-50 text-rose-800";
  }
  if (normalized === "degraded") {
    return "border-orange-300 bg-orange-50 text-orange-800";
  }
  if (normalized === "partial" || normalized === "skipped" || normalized === "not_assessed") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  if (normalized === "assessed_with_findings") {
    return "border-cyan-300 bg-cyan-50 text-cyan-800";
  }
  if (normalized === "healthy" || normalized === "assessed_no_issue") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  return "border-zinc-300 bg-zinc-100 text-zinc-700";
}

function severityBadgeClass(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (normalized === "error") {
    return "border-rose-300 bg-rose-50 text-rose-800";
  }
  if (normalized === "warning") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-zinc-300 bg-zinc-100 text-zinc-700";
}

function formatLabel(value: string | null): string {
  const normalized = (value ?? "").trim();
  if (!normalized) {
    return "-";
  }
  return normalized
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function isDegradedChecker(item: CoverageCheckerItem): boolean {
  return (
    item.status === "assessment_failed" ||
    item.status === "skipped" ||
    item.status === "not_assessed" ||
    item.permission_gap_count > 0
  );
}

/**
 * Detailed coverage visibility page for the latest run.
 */
export function CoverageClientPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const scope = getStoredScope();
  const auth = useAuth();
  const statusFilter = searchParams.get("status") ?? "";
  const serviceFilter = searchParams.get("service") ?? "";
  const regionFilter = searchParams.get("region") ?? "";
  const accountFilter = searchParams.get("account_id") ?? "";
  const checkerFilter = searchParams.get("checker_id") ?? "";
  const issueTypeFilter = searchParams.get("issue_type") ?? "";
  const limit = parsePositiveInt(searchParams.get("limit"), 200);
  const historyLimit = parsePositiveInt(searchParams.get("history_limit"), 12);
  const [serviceInput, setServiceInput] = useState(serviceFilter);
  const [regionInput, setRegionInput] = useState(regionFilter);
  const [accountInput, setAccountInput] = useState(accountFilter);
  const [checkerInput, setCheckerInput] = useState(checkerFilter);
  const permissions = useMemo(() => new Set(auth.user?.permissions ?? []), [auth.user?.permissions]);
  const canReadRuns = permissions.has("admin:full") || permissions.has("runs:read");
  const canReadFindings = permissions.has("admin:full") || permissions.has("findings:read");
  const canReadRecommendations = permissions.has("admin:full") || permissions.has("findings:read");
  const canReadUsers = permissions.has("admin:full") || permissions.has("users:read");

  const latestCoverage = useRunCoverageLatest(canReadRuns);
  const checkerCoverage = useRunCoverageCheckers(
    {
      status: statusFilter,
      service: serviceFilter,
      region: regionFilter,
      accountId: accountFilter,
      checkerId: checkerFilter,
      limit,
      offset: 0,
    },
    canReadRuns,
  );
  const coverageIssues = useRunCoverageIssues(
    {
      service: serviceFilter,
      region: regionFilter,
      accountId: accountFilter,
      checkerId: checkerFilter,
      issueType: issueTypeFilter,
      limit,
      offset: 0,
    },
    canReadRuns,
  );
  const serviceSummaries = useRunCoverageServices(
    {
      status: statusFilter,
      region: regionFilter,
      accountId: accountFilter,
    },
    canReadRuns,
  );
  const accountSummaries = useRunCoverageAccounts(
    {
      status: statusFilter,
      service: serviceFilter,
      region: regionFilter,
      accountId: accountFilter,
    },
    canReadRuns,
  );
  const coverageHistory = useRunCoverageHistory(
    {
      status: statusFilter,
      limit: historyLimit,
    },
    canReadRuns,
  );
  const coverageRegression = useRunCoverageRegressionLatest(canReadRuns);

  useEffect(() => {
    setServiceInput(serviceFilter);
  }, [serviceFilter]);

  useEffect(() => {
    setRegionInput(regionFilter);
  }, [regionFilter]);

  useEffect(() => {
    setAccountInput(accountFilter);
  }, [accountFilter]);

  useEffect(() => {
    setCheckerInput(checkerFilter);
  }, [checkerFilter]);

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

  const degradedCheckers = (checkerCoverage.data?.items ?? []).filter(isDegradedChecker);
  const permissionGapCheckers = (checkerCoverage.data?.items ?? []).filter(
    (item) => item.permission_gap_count > 0,
  );
  const failedCheckers = (checkerCoverage.data?.items ?? []).filter(
    (item) => item.status === "assessment_failed",
  );
  const skippedCheckers = (checkerCoverage.data?.items ?? []).filter(
    (item) => item.status === "skipped" || item.status === "not_assessed",
  );
  const regressionSummary = coverageRegression.data?.summary ?? null;

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
    router.push(query ? `/coverage?${query}` : "/coverage");
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    pushWithParams({
      service: serviceInput.trim() || null,
      region: regionInput.trim() || null,
      account_id: accountInput.trim() || null,
      checker_id: checkerInput.trim() || null,
    });
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
              Coverage Visibility
            </p>
            <h1 className="font-display mt-2 text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">
              Scan Coverage Control Room
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Tenant: <span className="font-medium">{scope.tenantId}</span> | Workspace:{" "}
              <span className="font-medium">{scope.workspace}</span>
            </p>
            <p className="text-sm text-slate-600">
              Latest run:{" "}
              <span className="font-medium">
                {latestCoverage.data?.run?.run_id ?? checkerCoverage.data?.run?.run_id ?? "-"}
              </span>{" "}
              | Run date:{" "}
              <span className="font-medium">
                {formatUtcDateTime(latestCoverage.data?.run?.run_ts ?? checkerCoverage.data?.run?.run_ts ?? null)}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-2 self-start">
            {canReadFindings ? (
              <button
                type="button"
                className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
                onClick={() => {
                  router.push("/dashboard");
                }}
              >
                Dashboard
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
            {canReadRecommendations ? (
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
            {canReadRecommendations ? (
              <button
                type="button"
                className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
                onClick={() => {
                  router.push("/remediations");
                }}
              >
                Realized Savings
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

        <RunCoverageBanner
          run={latestCoverage.data?.run ?? null}
          summary={latestCoverage.data?.coverage ?? null}
        />

        <section className="finops-panel mb-4 rounded-2xl p-4 text-sm">
          <div className="grid gap-3 md:grid-cols-4">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Status</span>
              <select
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={statusFilter}
                onChange={(event) => {
                  pushWithParams({ status: event.target.value || null });
                }}
              >
                <option value="">All</option>
                <option value="assessment_failed">Assessment Failed</option>
                <option value="skipped">Skipped</option>
                <option value="not_assessed">Not Assessed</option>
                <option value="assessed_with_findings">Assessed With Findings</option>
                <option value="assessed_no_issue">Assessed No Issue</option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Issue Type</span>
              <select
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={issueTypeFilter}
                onChange={(event) => {
                  pushWithParams({ issue_type: event.target.value || null });
                }}
              >
                <option value="">All</option>
                <option value="missing_permission">Missing Permission</option>
                <option value="throttled">Throttled</option>
                <option value="api_error">API Error</option>
                <option value="malformed_source_data">Malformed Source Data</option>
                <option value="unsupported_scope">Unsupported Scope</option>
                <option value="internal_checker_error">Internal Checker Error</option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Page Size</span>
              <select
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={String(limit)}
                onChange={(event) => {
                  pushWithParams({ limit: event.target.value });
                }}
              >
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="200">200</option>
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">History Size</span>
              <select
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={String(historyLimit)}
                onChange={(event) => {
                  pushWithParams({ history_limit: event.target.value });
                }}
              >
                <option value="6">6</option>
                <option value="12">12</option>
                <option value="20">20</option>
              </select>
            </label>
          </div>

          <form className="mt-3 grid gap-3 md:grid-cols-4" onSubmit={applyFilters}>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Service</span>
              <input
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={serviceInput}
                onChange={(event) => {
                  setServiceInput(event.target.value);
                }}
                placeholder="ec2, rds, lambda..."
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Region</span>
              <input
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={regionInput}
                onChange={(event) => {
                  setRegionInput(event.target.value);
                }}
                placeholder="eu-west-1"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Account</span>
              <input
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={accountInput}
                onChange={(event) => {
                  setAccountInput(event.target.value);
                }}
                placeholder="123456789012"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">Checker</span>
              <input
                className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                value={checkerInput}
                onChange={(event) => {
                  setCheckerInput(event.target.value);
                }}
                placeholder="aws.ec2.idle.instances"
              />
            </label>
            <div className="md:col-span-4 flex flex-wrap items-center gap-2">
              <button type="submit" className="rounded-lg border border-cyan-300 bg-cyan-50 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-cyan-800 transition hover:border-cyan-400 hover:bg-cyan-100">
                Apply
              </button>
              <button
                type="button"
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-700 transition hover:bg-slate-100"
                onClick={() => {
                  setServiceInput("");
                  setRegionInput("");
                  setAccountInput("");
                  setCheckerInput("");
                  pushWithParams({
                    status: null,
                    service: null,
                    region: null,
                    account_id: null,
                    checker_id: null,
                    issue_type: null,
                    limit: "200",
                    history_limit: String(historyLimit),
                  });
                }}
              >
                Clear
              </button>
            </div>
          </form>
        </section>

        <section className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Checker Targets</p>
            <p className="font-display mt-1 text-2xl font-semibold text-white">
              {checkerCoverage.data?.items.length ?? 0}
            </p>
          </article>
          <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Degraded Targets</p>
            <p className="font-display mt-1 text-2xl font-semibold text-white">{degradedCheckers.length}</p>
          </article>
          <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Permission Gap Targets</p>
            <p className="font-display mt-1 text-2xl font-semibold text-white">{permissionGapCheckers.length}</p>
          </article>
          <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Structured Issues</p>
            <p className="font-display mt-1 text-2xl font-semibold text-white">
              {coverageIssues.data?.items.length ?? 0}
            </p>
          </article>
        </section>

        <section className="mb-4 grid gap-3 lg:grid-cols-3">
          <article className="finops-panel rounded-2xl p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Assessment Failures</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{failedCheckers.length}</p>
            <p className="mt-1 text-sm text-slate-600">
              Targets that failed and should not be read as &quot;no findings&quot;.
            </p>
          </article>
          <article className="finops-panel rounded-2xl p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Skipped Or Not Assessed</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{skippedCheckers.length}</p>
            <p className="mt-1 text-sm text-slate-600">
              Intentional skips and incomplete coverage targets that reduce completeness.
            </p>
          </article>
          <article className="finops-panel rounded-2xl p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Permission Gaps</p>
            <p className="mt-2 text-2xl font-semibold text-slate-900">{permissionGapCheckers.length}</p>
            <p className="mt-1 text-sm text-slate-600">
              Targets where missing IAM permissions reduced visibility or confidence.
            </p>
          </article>
        </section>

        <section className="mb-4 grid gap-4 lg:grid-cols-2">
          <article className="finops-panel rounded-2xl p-4">
            <div className="mb-3">
              <h2 className="text-lg font-semibold text-slate-900">Service Rollup</h2>
              <p className="text-sm text-slate-600">
                Coverage grouped by service for the latest run and current filters.
              </p>
            </div>
            <div className="overflow-x-auto rounded-xl">
              <table className="min-w-full text-left text-sm text-slate-700">
                <thead className="finops-table-head text-xs uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Service</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Coverage</th>
                    <th className="px-3 py-2">Failed</th>
                    <th className="px-3 py-2">Permission Gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {(serviceSummaries.data?.items ?? []).map((item) => (
                    <tr key={item.service} className="border-t border-slate-100">
                      <td className="px-3 py-2">{item.service}</td>
                      <td className="px-3 py-2">
                        <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.coverage_status)}`}>
                          {formatLabel(item.coverage_status)}
                        </span>
                      </td>
                      <td className="px-3 py-2">{Number(item.coverage_pct ?? 0).toFixed(2)}%</td>
                      <td className="px-3 py-2">{item.assessment_failed}</td>
                      <td className="px-3 py-2">{item.permission_gap_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <article className="finops-panel rounded-2xl p-4">
            <div className="mb-3">
              <h2 className="text-lg font-semibold text-slate-900">Account Rollup</h2>
              <p className="text-sm text-slate-600">
                Coverage grouped by account and region for the latest run and current filters.
              </p>
            </div>
            <div className="overflow-x-auto rounded-xl">
              <table className="min-w-full text-left text-sm text-slate-700">
                <thead className="finops-table-head text-xs uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Account</th>
                    <th className="px-3 py-2">Region</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Coverage</th>
                    <th className="px-3 py-2">Failed</th>
                  </tr>
                </thead>
                <tbody>
                  {(accountSummaries.data?.items ?? []).map((item) => (
                    <tr key={`${item.account_id}:${item.region}`} className="border-t border-slate-100">
                      <td className="px-3 py-2">{item.account_id || "-"}</td>
                      <td className="px-3 py-2">{item.region || "-"}</td>
                      <td className="px-3 py-2">
                        <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.coverage_status)}`}>
                          {formatLabel(item.coverage_status)}
                        </span>
                      </td>
                      <td className="px-3 py-2">{Number(item.coverage_pct ?? 0).toFixed(2)}%</td>
                      <td className="px-3 py-2">{item.assessment_failed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        </section>

        <section className="mb-4 grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
          <article className="finops-panel rounded-2xl p-4">
            <div className="mb-3">
              <h2 className="text-lg font-semibold text-slate-900">Coverage History</h2>
              <p className="text-sm text-slate-600">
                Recent run-level coverage snapshots for the current tenant and workspace.
              </p>
            </div>
            <div className="overflow-x-auto rounded-xl">
              <table className="min-w-full text-left text-sm text-slate-700">
                <thead className="finops-table-head text-xs uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="px-3 py-2">Run</th>
                    <th className="px-3 py-2">Date</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Coverage</th>
                    <th className="px-3 py-2">Failed</th>
                    <th className="px-3 py-2">Permission Gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {(coverageHistory.data?.items ?? []).map((item) => (
                    <tr key={item.run_id} className="border-t border-slate-100">
                      <td className="px-3 py-2">
                        <span className="block max-w-[14rem] truncate" title={item.run_id}>
                          {item.run_id}
                        </span>
                      </td>
                      <td className="px-3 py-2">{formatUtcDateTime(item.run_ts)}</td>
                      <td className="px-3 py-2">
                        <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.coverage_status ?? "")}`}>
                          {formatLabel(item.coverage_status)}
                        </span>
                      </td>
                      <td className="px-3 py-2">{Number(item.coverage_pct ?? 0).toFixed(2)}%</td>
                      <td className="px-3 py-2">{item.assessment_failed}</td>
                      <td className="px-3 py-2">{item.permission_gap_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>

          <article className="finops-panel rounded-2xl p-4">
            <div className="mb-3">
              <h2 className="text-lg font-semibold text-slate-900">Regression Snapshot</h2>
              <p className="text-sm text-slate-600">
                Latest ready run compared with the previous ready run.
              </p>
            </div>
            {regressionSummary ? (
              <>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Severity</p>
                    <p className="mt-1 font-semibold text-slate-900">{formatLabel(regressionSummary.severity)}</p>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Checker Regressions</p>
                    <p className="mt-1 font-semibold text-slate-900">{coverageRegression.data?.checker_regressions.count ?? 0}</p>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Coverage Delta</p>
                    <p className="mt-1 font-semibold text-slate-900">{regressionSummary.coverage_pct_delta.toFixed(2)} pts</p>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Failure Delta</p>
                    <p className="mt-1 font-semibold text-slate-900">{regressionSummary.assessment_failed_delta}</p>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Permission Gap Delta</p>
                    <p className="mt-1 font-semibold text-slate-900">{regressionSummary.permission_gap_delta}</p>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Status Worsened</p>
                    <p className="mt-1 font-semibold text-slate-900">{regressionSummary.status_worsened ? "Yes" : "No"}</p>
                  </div>
                </div>

                <div className="mt-4">
                  <h3 className="text-sm font-semibold text-slate-900">Regressed Services</h3>
                  <div className="mt-2 overflow-x-auto rounded-xl">
                    <table className="min-w-full text-left text-sm text-slate-700">
                      <thead className="finops-table-head text-xs uppercase tracking-wide text-slate-600">
                        <tr>
                          <th className="px-3 py-2">Service</th>
                          <th className="px-3 py-2">Coverage Delta</th>
                          <th className="px-3 py-2">Failure Delta</th>
                          <th className="px-3 py-2">Permission Gap Delta</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(coverageRegression.data?.service_regressions ?? []).map((item) => (
                          <tr key={item.service} className="border-t border-slate-100">
                            <td className="px-3 py-2">{item.service}</td>
                            <td className="px-3 py-2">{item.coverage_pct_delta.toFixed(2)} pts</td>
                            <td className="px-3 py-2">{item.assessment_failed_delta}</td>
                            <td className="px-3 py-2">{item.permission_gap_delta}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {(coverageRegression.data?.service_regressions ?? []).length === 0 ? (
                    <p className="mt-2 rounded-xl bg-white/80 px-3 py-2 text-sm text-slate-600">
                      No service-level regressions were detected between the latest two ready runs.
                    </p>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="rounded-xl bg-white/80 px-3 py-2 text-sm text-slate-600">
                {coverageRegression.data?.message ?? "Need at least two ready runs to calculate regressions."}
              </p>
            )}
          </article>
        </section>

        {checkerCoverage.isLoading || coverageIssues.isLoading || coverageHistory.isLoading || coverageRegression.isLoading ? (
          <p className="rounded-xl bg-white/80 px-3 py-2 text-sm text-slate-700">
            Loading coverage details...
          </p>
        ) : null}

        {checkerCoverage.error ? (
          <div className="mb-4 rounded-xl border border-red-200 bg-red-50/95 p-3 text-sm text-red-700">
            <p>{apiErrorMessage("Failed to load checker coverage", checkerCoverage.error)}</p>
          </div>
        ) : null}

        {coverageIssues.error ? (
          <div className="mb-4 rounded-xl border border-red-200 bg-red-50/95 p-3 text-sm text-red-700">
            <p>{apiErrorMessage("Failed to load coverage issues", coverageIssues.error)}</p>
          </div>
        ) : null}

        {coverageHistory.error ? (
          <div className="mb-4 rounded-xl border border-red-200 bg-red-50/95 p-3 text-sm text-red-700">
            <p>{apiErrorMessage("Failed to load coverage history", coverageHistory.error)}</p>
          </div>
        ) : null}

        {coverageRegression.error ? (
          <div className="mb-4 rounded-xl border border-red-200 bg-red-50/95 p-3 text-sm text-red-700">
            <p>{apiErrorMessage("Failed to load coverage regressions", coverageRegression.error)}</p>
          </div>
        ) : null}

        <section className="finops-panel mb-4 rounded-2xl p-4">
          <div className="mb-3">
            <h2 className="text-lg font-semibold text-slate-900">Checker Coverage</h2>
            <p className="text-sm text-slate-600">
              One row per account, region, service, and checker target in the latest run.
            </p>
          </div>
          <div className="overflow-x-auto rounded-xl">
            <table className="min-w-full text-left text-sm text-slate-700">
              <thead className="finops-table-head text-xs uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Service</th>
                  <th className="px-3 py-2">Checker</th>
                  <th className="px-3 py-2">Region</th>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2">Findings</th>
                  <th className="px-3 py-2">Confidence</th>
                  <th className="px-3 py-2">Permission Gaps</th>
                  <th className="px-3 py-2">Error / Skip</th>
                  <th className="px-3 py-2">Duration</th>
                </tr>
              </thead>
              <tbody>
                {(checkerCoverage.data?.items ?? []).map((item) => (
                  <tr key={`${item.account_id}:${item.region}:${item.service}:${item.checker_id}`} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}>
                        {formatLabel(item.status)}
                      </span>
                    </td>
                    <td className="px-3 py-2">{item.service}</td>
                    <td className="px-3 py-2">
                      <span className="block max-w-[18rem] truncate" title={item.checker_id}>
                        {item.checker_id}
                      </span>
                    </td>
                    <td className="px-3 py-2">{item.region || "-"}</td>
                    <td className="px-3 py-2">{item.account_id || "-"}</td>
                    <td className="px-3 py-2">{item.findings_count}</td>
                    <td className="px-3 py-2">{formatLabel(item.confidence)}</td>
                    <td className="px-3 py-2">{item.permission_gap_count}</td>
                    <td className="px-3 py-2">
                      <span
                        className="block max-w-[18rem] truncate"
                        title={item.error_message ?? item.skip_reason ?? item.error_code ?? "-"}
                      >
                        {item.error_class ?? item.skip_reason ?? item.error_code ?? "-"}
                      </span>
                    </td>
                    <td className="px-3 py-2">{formatDurationMs(item.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(checkerCoverage.data?.items ?? []).length === 0 ? (
            <p className="mt-3 rounded-xl bg-white/80 px-3 py-2 text-sm text-slate-600">
              No checker coverage rows are available for the latest run.
            </p>
          ) : null}
        </section>

        <section className="finops-panel rounded-2xl p-4">
          <div className="mb-3">
            <h2 className="text-lg font-semibold text-slate-900">Coverage Issues</h2>
            <p className="text-sm text-slate-600">
              Structured execution issues such as permission gaps, throttling, or upstream API failures.
            </p>
          </div>
          <div className="overflow-x-auto rounded-xl">
            <table className="min-w-full text-left text-sm text-slate-700">
              <thead className="finops-table-head text-xs uppercase tracking-wide text-slate-600">
                <tr>
                  <th className="px-3 py-2">Severity</th>
                  <th className="px-3 py-2">Issue Type</th>
                  <th className="px-3 py-2">Service</th>
                  <th className="px-3 py-2">Checker</th>
                  <th className="px-3 py-2">Region</th>
                  <th className="px-3 py-2">Operation</th>
                  <th className="px-3 py-2">Code</th>
                  <th className="px-3 py-2">Retryable</th>
                  <th className="px-3 py-2">Message</th>
                </tr>
              </thead>
              <tbody>
                {(coverageIssues.data?.items ?? []).map((item, index) => (
                  <tr key={`${item.service}:${item.checker_id}:${item.issue_type}:${item.error_code ?? index}`} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${severityBadgeClass(item.severity)}`}>
                        {formatLabel(item.severity)}
                      </span>
                    </td>
                    <td className="px-3 py-2">{formatLabel(item.issue_type)}</td>
                    <td className="px-3 py-2">{item.service}</td>
                    <td className="px-3 py-2">
                      <span className="block max-w-[18rem] truncate" title={item.checker_id}>
                        {item.checker_id}
                      </span>
                    </td>
                    <td className="px-3 py-2">{item.region || "-"}</td>
                    <td className="px-3 py-2">{item.operation || "-"}</td>
                    <td className="px-3 py-2">{item.error_code || "-"}</td>
                    <td className="px-3 py-2">{item.is_retryable ? "Yes" : "No"}</td>
                    <td className="px-3 py-2">
                      <span className="block max-w-[22rem] truncate" title={item.message ?? "-"}>
                        {item.message ?? "-"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(coverageIssues.data?.items ?? []).length === 0 ? (
            <p className="mt-3 rounded-xl bg-white/80 px-3 py-2 text-sm text-slate-600">
              No structured coverage issues were recorded for the latest run.
            </p>
          ) : null}
        </section>
      </div>
    </main>
  );
}
