"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface RunLatestItem {
  tenant_id: string;
  workspace: string;
  run_id: string;
  run_ts: string;
  status: string | null;
  artifact_prefix: string | null;
  ingested_at: string | null;
  engine_version: string | null;
  pricing_version: string | null;
  pricing_source: string | null;
  raw_present: boolean | null;
  correlated_present: boolean | null;
  enriched_present: boolean | null;
  coverage_pct: number | null;
  coverage_status: string | null;
  coverage_targets: number | null;
  coverage_failed: number | null;
  permission_gap_count: number | null;
}

interface RunLatestResponse {
  tenant_id: string;
  workspace: string;
  run: RunLatestItem | null;
}

function asNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeRun(run: RunLatestItem | null): RunLatestItem | null {
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

/**
 * Resolve latest run metadata for current tenant/workspace.
 */
export function useRunsLatest(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: ["runs", "latest", scope?.tenantId, scope?.workspace],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        const response = await apiClient.get<RunLatestResponse>("/runs/latest");
        return normalizeRun(response.run);
      } catch (error) {
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          return null;
        }
        throw error;
      }
    },
  });
}
