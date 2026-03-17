"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface CoverageLatestRunRef {
  run_id: string;
  run_ts: string;
}

export interface CoverageCheckerItem {
  account_id: string | null;
  region: string | null;
  service: string;
  checker_id: string;
  checker_scope: string;
  status: string;
  findings_count: number;
  duration_ms: number | null;
  confidence: string;
  completeness_pct: number | null;
  permission_gap_count: number;
  error_class: string | null;
  error_code: string | null;
  error_message: string | null;
  skip_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface CoverageIssueItem {
  account_id: string | null;
  region: string | null;
  service: string;
  checker_id: string;
  issue_type: string;
  operation: string | null;
  error_code: string | null;
  message: string | null;
  is_retryable: boolean;
  severity: string;
  payload: Record<string, unknown> | null;
  created_at: string | null;
}

interface CoverageCheckersResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  run: CoverageLatestRunRef | null;
  items: CoverageCheckerItem[];
}

interface CoverageIssuesResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  run: CoverageLatestRunRef | null;
  items: CoverageIssueItem[];
}

/**
 * Resolve checker-level latest coverage rows for the active scope.
 */
export function useRunCoverageCheckers(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: ["runs", "latest", "coverage", "checkers", scope?.tenantId, scope?.workspace],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageCheckersResponse>("/runs/latest/coverage/checkers");
      } catch (error) {
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          return null;
        }
        throw error;
      }
    },
  });
}

/**
 * Resolve structured latest coverage issues for the active scope.
 */
export function useRunCoverageIssues(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: ["runs", "latest", "coverage", "issues", scope?.tenantId, scope?.workspace],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageIssuesResponse>("/runs/latest/coverage/issues");
      } catch (error) {
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          return null;
        }
        throw error;
      }
    },
  });
}
