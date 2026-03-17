"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo } from "react";

import { RunCoverageBanner } from "@/components/coverage/RunCoverageBanner";
import { useAuth } from "@/hooks/useAuth";
import {
  CoverageCheckerItem,
  useRunCoverageCheckers,
  useRunCoverageIssues,
} from "@/hooks/useRunCoverageDetails";
import { useRunCoverageLatest } from "@/hooks/useRunCoverageLatest";
import { ApiError } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

function formatDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
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
  if (normalized === "assessment_failed") {
    return "border-rose-300 bg-rose-50 text-rose-800";
  }
  if (normalized === "skipped" || normalized === "not_assessed") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  if (normalized === "assessed_with_findings") {
    return "border-cyan-300 bg-cyan-50 text-cyan-800";
  }
  if (normalized === "assessed_no_issue") {
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
  const scope = getStoredScope();
  const auth = useAuth();
  const permissions = useMemo(() => new Set(auth.user?.permissions ?? []), [auth.user?.permissions]);
  const canReadRuns = permissions.has("admin:full") || permissions.has("runs:read");
  const canReadFindings = permissions.has("admin:full") || permissions.has("findings:read");
  const canReadRecommendations = permissions.has("admin:full") || permissions.has("findings:read");
  const canReadUsers = permissions.has("admin:full") || permissions.has("users:read");

  const latestCoverage = useRunCoverageLatest(canReadRuns);
  const checkerCoverage = useRunCoverageCheckers(canReadRuns);
  const coverageIssues = useRunCoverageIssues(canReadRuns);

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
                {formatDateTime(latestCoverage.data?.run?.run_ts ?? checkerCoverage.data?.run?.run_ts ?? null)}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-2 self-start">
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
              Targets that failed and should not be read as "no findings".
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

        {checkerCoverage.isLoading || coverageIssues.isLoading ? (
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
