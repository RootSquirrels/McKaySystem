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
        return await apiClient.get<RunCoverageLatestResponse>("/runs/latest/coverage");
      } catch (error) {
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          return null;
        }
        throw error;
      }
    },
  });
}
