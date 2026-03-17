"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface GraphContextResource {
  resource_key: string;
  provider?: string | null;
  service: string;
  resource_type: string;
  account_id?: string | null;
  region?: string | null;
  resource_id?: string | null;
  resource_arn?: string | null;
  resource_name?: string | null;
  parent_resource_key?: string | null;
  state?: string | null;
  owner_hint?: string | null;
  is_deleted?: boolean | null;
  latest_run_id?: string | null;
  latest_run_ts?: string | null;
}

export interface GraphContextNeighbor {
  edge_key: string;
  edge_type: string;
  direction: string;
  directionality: string;
  confidence: string;
  source_kind: string;
  service: string;
  account_id: string;
  region: string;
  resource: GraphContextResource;
}

export interface RunGraphContextLatestResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  run: {
    run_id: string;
    run_ts: string;
  } | null;
  resource: GraphContextResource | null;
  neighbors: GraphContextNeighbor[];
  total_neighbors: number;
  neighbor_limit: number;
}

/**
 * Resolve bounded latest graph context for one resource key in the active scope.
 */
export function useRunGraphContextLatest(resourceKey: string | null, enabled = true, neighborLimit = 12) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "runs",
      "latest",
      "graph",
      "context",
      scope?.tenantId,
      scope?.workspace,
      resourceKey,
      neighborLimit,
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && resourceKey && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<RunGraphContextLatestResponse>("/runs/latest/graph/context", {
          query: {
            resource_key: resourceKey,
            neighbor_limit: neighborLimit,
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
