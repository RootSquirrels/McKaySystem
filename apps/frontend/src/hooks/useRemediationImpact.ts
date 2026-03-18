"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface RemediationImpactItem {
  tenant_id: string;
  workspace: string;
  action_id: string;
  fingerprint: string;
  check_id: string;
  action_type: string;
  action_status: string;
  verification_status: string;
  baseline_estimated_monthly_savings: number;
  current_estimated_monthly_savings: number | null;
  realized_monthly_savings: number;
  realization_rate_pct: number | null;
  latest_run_id: string | null;
  latest_run_ts: string | null;
  present_in_latest: boolean | null;
  finalized_at: string;
  computed_at: string;
  version: number;
  outcome_status: string;
  outcome_label: string;
  realization_band: string;
  estimated_not_realized_monthly_savings: number;
  savings_delta_monthly: number;
}

export interface RemediationImpactSummary {
  actions_count: number;
  resolved_count: number;
  persistent_count: number;
  pending_count: number;
  failed_count: number;
  fully_realized_count: number;
  partial_realization_count: number;
  no_realization_count: number;
  baseline_total_monthly_savings: number;
  realized_total_monthly_savings: number;
  estimated_not_realized_monthly_savings: number;
  realization_rate_pct: number | null;
}

export interface RemediationImpactQualityRow {
  group_key: string;
  actions_count: number;
  fully_realized_count: number;
  partial_realization_count: number;
  no_realization_count: number;
  pending_count: number;
  failed_count: number;
  baseline_total_monthly_savings: number;
  realized_total_monthly_savings: number;
  estimated_not_realized_monthly_savings: number;
  realization_rate_pct: number | null;
  effective_success_rate_pct: number | null;
}

export interface RemediationImpactResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  limit: number;
  offset: number;
  total: number;
  refreshed: number;
  summary: RemediationImpactSummary;
  quality: {
    by_recommendation_type: RemediationImpactQualityRow[];
    by_check_id: RemediationImpactQualityRow[];
  };
  items: RemediationImpactItem[];
}

interface UseRemediationImpactOptions {
  limit?: number;
  offset?: number;
  actionStatus?: string;
  verificationStatus?: string;
  actionType?: string;
  checkId?: string;
  refresh?: boolean;
}

export function remediationImpactQueryKey(
  scope: { tenantId?: string; workspace?: string },
  options: {
    limit: number;
    offset: number;
    actionStatus: string;
    verificationStatus: string;
    actionType: string;
    checkId: string;
    refresh: boolean;
  },
) {
  return [
    "remediation-impact",
    scope.tenantId,
    scope.workspace,
    options.limit,
    options.offset,
    options.actionStatus,
    options.verificationStatus,
    options.actionType,
    options.checkId,
    options.refresh,
  ] as const;
}

/**
 * Query scoped realized-savings remediation impact rows.
 */
export function useRemediationImpact(options: UseRemediationImpactOptions = {}) {
  const scope = getStoredScope();
  const limit = options.limit ?? 50;
  const offset = options.offset ?? 0;
  const actionStatus = options.actionStatus ?? "";
  const verificationStatus = options.verificationStatus ?? "";
  const actionType = options.actionType ?? "";
  const checkId = options.checkId ?? "";
  const refresh = options.refresh ?? false;

  return useQuery({
    queryKey: remediationImpactQueryKey(
      { tenantId: scope?.tenantId, workspace: scope?.workspace },
      {
        limit,
        offset,
        actionStatus,
        verificationStatus,
        actionType,
        checkId,
        refresh,
      },
    ),
    enabled: Boolean(scope?.tenantId && scope?.workspace),
    queryFn: async () => {
      return apiClient.get<RemediationImpactResponse>("/remediations/impact", {
        query: {
          limit,
          offset,
          action_status: actionStatus,
          verification_status: verificationStatus,
          action_type: actionType,
          check_id: checkId,
          refresh,
        },
      });
    },
  });
}
