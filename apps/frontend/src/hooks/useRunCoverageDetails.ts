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

export interface CoverageSummaryItem {
  targets_total: number;
  assessed_total: number;
  assessment_failed: number;
  skipped_total: number;
  not_assessed_total: number;
  permission_gap_count: number;
  coverage_pct: number;
  coverage_status: string;
}

export interface CoverageServiceSummaryItem extends CoverageSummaryItem {
  service: string;
}

export interface CoverageAccountSummaryItem extends CoverageSummaryItem {
  account_id: string | null;
  region: string | null;
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

interface CoverageServicesResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  run: CoverageLatestRunRef | null;
  items: CoverageServiceSummaryItem[];
}

interface CoverageAccountsResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  run: CoverageLatestRunRef | null;
  items: CoverageAccountSummaryItem[];
}

export interface CoverageHistoryItem {
  run_id: string;
  run_ts: string;
  status: string | null;
  targets_total: number;
  assessed_total: number;
  assessment_failed: number;
  skipped_total: number;
  not_assessed_total: number;
  permission_gap_count: number;
  coverage_pct: number;
  coverage_status: string | null;
  confidence: string;
}

export interface CoverageRegressionSummary {
  latest: CoverageHistoryItem;
  previous: CoverageHistoryItem;
  coverage_pct_delta: number;
  assessment_failed_delta: number;
  permission_gap_delta: number;
  status_worsened: boolean;
  severity: string;
}

export interface CoverageServiceRegressionItem {
  service: string;
  latest: CoverageServiceSummaryItem;
  previous: CoverageServiceSummaryItem;
  coverage_pct_delta: number;
  assessment_failed_delta: number;
  permission_gap_delta: number;
  status_worsened: boolean;
}

interface CoverageHistoryResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  items: CoverageHistoryItem[];
  limit: number;
}

interface CoverageRegressionResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  runs: CoverageLatestRunRef[];
  summary: CoverageRegressionSummary | null;
  service_regressions: CoverageServiceRegressionItem[];
  checker_regressions: { count: number };
  message?: string;
}

interface CoverageFilters {
  status?: string;
  service?: string;
  region?: string;
  accountId?: string;
  checkerId?: string;
  issueType?: string;
  limit?: number;
  offset?: number;
  dateFrom?: string;
  dateTo?: string;
}

/**
 * Resolve checker-level latest coverage rows for the active scope.
 */
export function useRunCoverageCheckers(filters: CoverageFilters = {}, enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "runs",
      "latest",
      "coverage",
      "checkers",
      scope?.tenantId,
      scope?.workspace,
      filters.status ?? "",
      filters.service ?? "",
      filters.region ?? "",
      filters.accountId ?? "",
      filters.checkerId ?? "",
      filters.limit ?? 200,
      filters.offset ?? 0,
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageCheckersResponse>("/runs/latest/coverage/checkers", {
          query: {
            status: filters.status,
            service: filters.service,
            region: filters.region,
            account_id: filters.accountId,
            checker_id: filters.checkerId,
            limit: filters.limit ?? 200,
            offset: filters.offset ?? 0,
          },
        });
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
export function useRunCoverageIssues(filters: CoverageFilters = {}, enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "runs",
      "latest",
      "coverage",
      "issues",
      scope?.tenantId,
      scope?.workspace,
      filters.service ?? "",
      filters.region ?? "",
      filters.accountId ?? "",
      filters.checkerId ?? "",
      filters.issueType ?? "",
      filters.limit ?? 200,
      filters.offset ?? 0,
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageIssuesResponse>("/runs/latest/coverage/issues", {
          query: {
            service: filters.service,
            region: filters.region,
            account_id: filters.accountId,
            checker_id: filters.checkerId,
            issue_type: filters.issueType,
            limit: filters.limit ?? 200,
            offset: filters.offset ?? 0,
          },
        });
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
 * Resolve service-level latest coverage summaries for the active scope.
 */
export function useRunCoverageServices(filters: CoverageFilters = {}, enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "runs",
      "latest",
      "coverage",
      "services",
      scope?.tenantId,
      scope?.workspace,
      filters.status ?? "",
      filters.region ?? "",
      filters.accountId ?? "",
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageServicesResponse>("/runs/latest/coverage/services", {
          query: {
            status: filters.status,
            region: filters.region,
            account_id: filters.accountId,
          },
        });
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
 * Resolve account-level latest coverage summaries for the active scope.
 */
export function useRunCoverageAccounts(filters: CoverageFilters = {}, enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "runs",
      "latest",
      "coverage",
      "accounts",
      scope?.tenantId,
      scope?.workspace,
      filters.status ?? "",
      filters.service ?? "",
      filters.region ?? "",
      filters.accountId ?? "",
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageAccountsResponse>("/runs/latest/coverage/accounts", {
          query: {
            status: filters.status,
            service: filters.service,
            region: filters.region,
            account_id: filters.accountId,
          },
        });
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
 * Resolve bounded coverage history for the active scope.
 */
export function useRunCoverageHistory(filters: CoverageFilters = {}, enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "runs",
      "coverage",
      "history",
      scope?.tenantId,
      scope?.workspace,
      filters.status ?? "",
      filters.dateFrom ?? "",
      filters.dateTo ?? "",
      filters.limit ?? 20,
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageHistoryResponse>("/runs/coverage/history", {
          query: {
            status: filters.status,
            date_from: filters.dateFrom,
            date_to: filters.dateTo,
            limit: filters.limit ?? 20,
          },
        });
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
 * Resolve latest coverage regressions compared with the previous ready run.
 */
export function useRunCoverageRegressionLatest(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: ["runs", "coverage", "regressions", "latest", scope?.tenantId, scope?.workspace],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<CoverageRegressionResponse>("/runs/coverage/regressions/latest");
      } catch (error) {
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          return null;
        }
        throw error;
      }
    },
  });
}
