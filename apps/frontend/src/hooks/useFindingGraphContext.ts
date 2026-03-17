"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";
import type { RunGraphContextLatestResponse } from "@/hooks/useRunGraphContextLatest";

export interface FindingGraphContextResponse extends Omit<RunGraphContextLatestResponse, "run"> {
  fingerprint: string;
  run_id: string | null;
  resource_key: string | null;
}

/**
 * Resolve bounded graph context for one finding fingerprint in the active scope.
 */
export function useFindingGraphContext(fingerprint: string | null, enabled = true, neighborLimit = 12) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: [
      "findings",
      "graph",
      scope?.tenantId,
      scope?.workspace,
      fingerprint,
      neighborLimit,
    ],
    enabled: Boolean(scope?.tenantId && scope?.workspace && fingerprint && enabled),
    retry: false,
    queryFn: async () => {
      try {
        return await apiClient.get<FindingGraphContextResponse>(`/findings/${fingerprint}/graph`, {
          query: {
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
