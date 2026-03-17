"use client";

import { ApiError } from "@/lib/api/client";
import { useFindingGraphContext } from "@/hooks/useFindingGraphContext";
import { graphResourceKeyFromPayload } from "@/lib/graph/resourceKey";
import { useRunGraphContextLatest } from "@/hooks/useRunGraphContextLatest";

function graphErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code})` : "";
    return `Failed to load related resources [${error.status}${code}]: ${error.message}`;
  }
  if (error instanceof Error) {
    return `Failed to load related resources: ${error.message}`;
  }
  return "Failed to load related resources.";
}

function firstNonEmptyText(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) {
      return text;
    }
  }
  return null;
}

export function ResourceGraphContextPanel({
  findingFingerprint,
  payload,
  accountId,
  region,
  service,
  enabled,
}: {
  findingFingerprint?: string | null;
  payload: Record<string, unknown> | null;
  accountId?: string | null;
  region?: string | null;
  service?: string | null;
  enabled: boolean;
}) {
  const resourceKey = graphResourceKeyFromPayload(payload, { accountId, region, service });
  const findingGraphContext = useFindingGraphContext(findingFingerprint ?? null, enabled);
  const resourceGraphContext = useRunGraphContextLatest(
    findingFingerprint ? null : resourceKey,
    enabled,
  );
  const graphContext = findingFingerprint ? findingGraphContext : resourceGraphContext;
  const resolvedResourceKey = findingFingerprint
    ? (findingGraphContext.data?.resource_key ?? resourceKey)
    : resourceKey;

  return (
    <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
      <h3 className="text-sm font-semibold text-slate-900">Related Resources</h3>
      {!resolvedResourceKey ? (
        <p className="mt-1 text-sm text-slate-600">
          This item does not expose a stable primary resource identity yet, so graph context is unavailable.
        </p>
      ) : null}
      {resolvedResourceKey ? (
        <p className="mt-1 break-all text-xs text-slate-500">{resolvedResourceKey}</p>
      ) : null}
      {graphContext.isLoading && resolvedResourceKey ? (
        <p className="mt-2 text-sm text-slate-600">Loading related resources...</p>
      ) : null}
      {graphContext.error && resolvedResourceKey ? (
        <p className="mt-2 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
          {graphErrorMessage(graphContext.error)}
        </p>
      ) : null}
      {!graphContext.isLoading && !graphContext.error && resolvedResourceKey && graphContext.data?.resource && (graphContext.data.neighbors?.length ?? 0) === 0 ? (
        <p className="mt-2 text-sm text-slate-600">
          No direct related resources were found in the latest graph snapshot.
        </p>
      ) : null}
      {graphContext.data?.resource ? (
        <div className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
          <p>
            <span className="font-medium">Primary:</span>{" "}
            {firstNonEmptyText(
              graphContext.data.resource.resource_name,
              graphContext.data.resource.resource_id,
              graphContext.data.resource.resource_arn,
              graphContext.data.resource.resource_key,
            )}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {graphContext.data.resource.service} | {graphContext.data.resource.resource_type}
          </p>
        </div>
      ) : null}
      {graphContext.data?.neighbors?.length ? (
        <div className="mt-3 space-y-2">
          {graphContext.data.neighbors.map((neighbor) => (
            <div
              key={neighbor.edge_key}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium text-slate-900">
                  {firstNonEmptyText(
                    neighbor.resource.resource_name,
                    neighbor.resource.resource_id,
                    neighbor.resource.resource_arn,
                    neighbor.resource.resource_key,
                  )}
                </p>
                <span className="rounded-full border border-cyan-200 bg-cyan-50 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-cyan-800">
                  {neighbor.edge_type}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {neighbor.direction} | {neighbor.resource.service} | {neighbor.resource.resource_type} | {neighbor.source_kind}
              </p>
              {neighbor.resource.state ? (
                <p className="mt-1 text-xs text-slate-500">State: {neighbor.resource.state}</p>
              ) : null}
            </div>
          ))}
          {graphContext.data.total_neighbors > graphContext.data.neighbors.length ? (
            <p className="text-xs text-slate-500">
              Showing {graphContext.data.neighbors.length} of {graphContext.data.total_neighbors} direct relationships.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
