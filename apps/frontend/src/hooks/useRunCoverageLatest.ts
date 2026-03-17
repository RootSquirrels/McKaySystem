"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface RunCoverageLatestRun {
  tenant_id: string;
  workspace: string;
  run_id: string;
  run_ts: string;
  status: string | null;
  coverage_pct: number | null;
  coverage_status: string | null;
  coverage_targets: number | null;
  coverage_failed: number | null;
  permission_gap_count: number | null;
}

export interface RunCoverageSummary {
  targets_total: number;
  assessed_total: number;
  assessed_with_findings: number;
  assessed_no_issue: number;
  assessment_failed: number;
  skipped_total: number;
  not_assessed_total: number;
  permission_gap_count: number;
  coverage_pct: number;
  coverage_status: string;
  confidence: string;
}

export interface RunCoverageLatestResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  run: RunCoverageLatestRun | null;
  coverage: RunCoverageSummary | null;
}

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeRun(run: RunCoverageLatestRun | null): RunCoverageLatestRun | null {
  if (!run) {
    return null;
  }
  return {
    ...run,
    coverage_pct: asNumber(run.coverage_pct),
    coverage_targets: asNumber(run.coverage_targets),
    coverage_failed: asNumber(run.coverage_failed),
    permission_gap_count: asNumber(run.permission_gap_count),
  };
}

function normalizeSummary(summary: RunCoverageSummary | null): RunCoverageSummary | null {
  if (!summary) {
    return null;
  }
  return {
    ...summary,
    targets_total: Number(summary.targets_total ?? 0),
    assessed_total: Number(summary.assessed_total ?? 0),
    assessed_with_findings: Number(summary.assessed_with_findings ?? 0),
    assessed_no_issue: Number(summary.assessed_no_issue ?? 0),
    assessment_failed: Number(summary.assessment_failed ?? 0),
    skipped_total: Number(summary.skipped_total ?? 0),
    not_assessed_total: Number(summary.not_assessed_total ?? 0),
    permission_gap_count: Number(summary.permission_gap_count ?? 0),
    coverage_pct: Number(summary.coverage_pct ?? 0),
  };
}

/**
 * Resolve the latest run coverage summary for the active tenant/workspace.
 */
export function useRunCoverageLatest(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: ["runs", "latest", "coverage", scope?.tenantId, scope?.workspace],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        const response = await apiClient.get<RunCoverageLatestResponse>("/runs/latest/coverage");
        return {
          ...response,
          run: normalizeRun(response.run),
          coverage: normalizeSummary(response.coverage),
        };
      } catch (error) {
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          return null;
        }
        throw error;
      }
    },
  });
}
