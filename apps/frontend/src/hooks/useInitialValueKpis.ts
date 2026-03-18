"use client";

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface InitialValueLatestRun {
  run_id: string | null;
  run_ts: string | null;
}

export interface FindingsKpiFamily {
  source: string;
  definition: string;
  open_findings_count: number;
  needs_attention_count: number;
  estimated_monthly_savings: number;
}

export interface RecommendationsKpiFamily {
  source: string;
  definition: string;
  eligible_recommendations_count: number;
  priority_p1_count: number;
  estimated_monthly_savings: number;
}

export interface PotentialSavingsKpiFamily {
  source: string;
  definition: string;
  actionable_opportunity_count: number;
  package_count: number;
  suppressed_leaf_count: number;
  estimated_monthly_savings: number;
  estimated_annual_savings: number;
}

export interface RealizedKpiFamily {
  source: string;
  definition: string;
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

export interface CoverageKpiFamily {
  source: string;
  definition: string;
  coverage_pct: number;
  coverage_status: string | null;
  permission_gap_count: number;
  assessment_failed: number;
  targets_total: number;
  assessed_total: number;
  confidence: string | null;
  latest_run_id: string | null;
  latest_run_ts: string | null;
}

export interface InitialValueKpisResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  latest_run: InitialValueLatestRun | null;
  kpis: {
    findings: FindingsKpiFamily;
    recommendations: RecommendationsKpiFamily;
    potential_savings: PotentialSavingsKpiFamily;
    realized: RealizedKpiFamily;
    coverage: CoverageKpiFamily;
  };
  trend: {
    latest_run: InitialValueLatestRun;
    previous_run: InitialValueLatestRun;
    findings: {
      definition: string;
      new_count: number;
      disappeared_count: number;
      net_change: number;
    };
    recommendations: {
      definition: string;
      eligible_count_delta: number;
      estimated_monthly_savings_delta: number;
    };
    coverage: {
      definition: string;
      coverage_pct_delta: number;
      assessment_failed_delta: number;
      permission_gap_delta: number;
      latest_coverage_status: string | null;
      previous_coverage_status: string | null;
    } | null;
  } | null;
  notes: string[];
}

function asNumber(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function asNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeResponse(response: InitialValueKpisResponse): InitialValueKpisResponse {
  return {
    ...response,
    latest_run: response.latest_run
      ? {
          run_id: response.latest_run.run_id ?? null,
          run_ts: response.latest_run.run_ts ?? null,
        }
      : null,
    kpis: {
      findings: {
        ...response.kpis.findings,
        open_findings_count: asNumber(response.kpis.findings.open_findings_count),
        needs_attention_count: asNumber(response.kpis.findings.needs_attention_count),
        estimated_monthly_savings: asNumber(response.kpis.findings.estimated_monthly_savings),
      },
      recommendations: {
        ...response.kpis.recommendations,
        eligible_recommendations_count: asNumber(response.kpis.recommendations.eligible_recommendations_count),
        priority_p1_count: asNumber(response.kpis.recommendations.priority_p1_count),
        estimated_monthly_savings: asNumber(response.kpis.recommendations.estimated_monthly_savings),
      },
      potential_savings: {
        ...response.kpis.potential_savings,
        actionable_opportunity_count: asNumber(response.kpis.potential_savings.actionable_opportunity_count),
        package_count: asNumber(response.kpis.potential_savings.package_count),
        suppressed_leaf_count: asNumber(response.kpis.potential_savings.suppressed_leaf_count),
        estimated_monthly_savings: asNumber(response.kpis.potential_savings.estimated_monthly_savings),
        estimated_annual_savings: asNumber(response.kpis.potential_savings.estimated_annual_savings),
      },
      realized: {
        ...response.kpis.realized,
        actions_count: asNumber(response.kpis.realized.actions_count),
        resolved_count: asNumber(response.kpis.realized.resolved_count),
        persistent_count: asNumber(response.kpis.realized.persistent_count),
        pending_count: asNumber(response.kpis.realized.pending_count),
        failed_count: asNumber(response.kpis.realized.failed_count),
        fully_realized_count: asNumber(response.kpis.realized.fully_realized_count),
        partial_realization_count: asNumber(response.kpis.realized.partial_realization_count),
        no_realization_count: asNumber(response.kpis.realized.no_realization_count),
        baseline_total_monthly_savings: asNumber(response.kpis.realized.baseline_total_monthly_savings),
        realized_total_monthly_savings: asNumber(response.kpis.realized.realized_total_monthly_savings),
        estimated_not_realized_monthly_savings: asNumber(
          response.kpis.realized.estimated_not_realized_monthly_savings,
        ),
        realization_rate_pct: asNullableNumber(response.kpis.realized.realization_rate_pct),
      },
      coverage: {
        ...response.kpis.coverage,
        coverage_pct: asNumber(response.kpis.coverage.coverage_pct),
        permission_gap_count: asNumber(response.kpis.coverage.permission_gap_count),
        assessment_failed: asNumber(response.kpis.coverage.assessment_failed),
        targets_total: asNumber(response.kpis.coverage.targets_total),
        assessed_total: asNumber(response.kpis.coverage.assessed_total),
      },
    },
    trend: response.trend
      ? {
          latest_run: {
            run_id: response.trend.latest_run.run_id ?? null,
            run_ts: response.trend.latest_run.run_ts ?? null,
          },
          previous_run: {
            run_id: response.trend.previous_run.run_id ?? null,
            run_ts: response.trend.previous_run.run_ts ?? null,
          },
          findings: {
            ...response.trend.findings,
            new_count: asNumber(response.trend.findings.new_count),
            disappeared_count: asNumber(response.trend.findings.disappeared_count),
            net_change: asNumber(response.trend.findings.net_change),
          },
          recommendations: {
            ...response.trend.recommendations,
            eligible_count_delta: asNumber(response.trend.recommendations.eligible_count_delta),
            estimated_monthly_savings_delta: asNumber(response.trend.recommendations.estimated_monthly_savings_delta),
          },
          coverage: response.trend.coverage
            ? {
                ...response.trend.coverage,
                coverage_pct_delta: asNumber(response.trend.coverage.coverage_pct_delta),
                assessment_failed_delta: asNumber(response.trend.coverage.assessment_failed_delta),
                permission_gap_delta: asNumber(response.trend.coverage.permission_gap_delta),
              }
            : null,
        }
      : null,
  };
}

export function useInitialValueKpis(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: ["kpis", "initial-value", scope?.tenantId, scope?.workspace],
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    queryFn: async () => {
      const response = await apiClient.get<InitialValueKpisResponse>("/kpis/initial-value");
      return normalizeResponse(response);
    },
  });
}
