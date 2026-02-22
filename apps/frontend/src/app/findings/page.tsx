"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/hooks/useAuth";
import { useFindings } from "@/hooks/useFindings";
import { getStoredScope } from "@/lib/scope";

function formatMoney(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

export default function FindingsPage() {
  const router = useRouter();
  const scope = getStoredScope();
  const auth = useAuth();
  const findings = useFindings({ limit: 50, offset: 0 });

  useEffect(() => {
    if (!scope) {
      router.replace("/login");
      return;
    }
    if (!auth.isLoading && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.isAuthenticated, auth.isLoading, router, scope]);

  if (!scope) {
    return null;
  }

  return (
    <main className="mx-auto min-h-screen w-full max-w-6xl px-6 py-8">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Findings</h1>
          <p className="text-sm text-zinc-600">
            Tenant: <span className="font-medium">{scope.tenantId}</span> | Workspace:{" "}
            <span className="font-medium">{scope.workspace}</span>
          </p>
        </div>
        <button
          type="button"
          className="rounded border border-zinc-300 px-3 py-2 text-sm"
          onClick={async () => {
            await auth.logout();
            router.push("/login");
          }}
        >
          Logout
        </button>
      </header>

      {findings.isLoading ? <p>Loading findings...</p> : null}
      {findings.error ? <p className="text-sm text-red-600">Failed to load findings.</p> : null}

      {!findings.isLoading && findings.data ? (
        <div className="overflow-x-auto rounded border border-zinc-200">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-600">
              <tr>
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Service</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Savings</th>
                <th className="px-3 py-2">State</th>
                <th className="px-3 py-2">Region</th>
              </tr>
            </thead>
            <tbody>
              {findings.data.items.map((item) => (
                <tr key={item.fingerprint} className="border-t border-zinc-100">
                  <td className="px-3 py-2">{item.severity}</td>
                  <td className="px-3 py-2">{item.service}</td>
                  <td className="px-3 py-2">{item.title}</td>
                  <td className="px-3 py-2">{formatMoney(item.estimated_monthly_savings)}</td>
                  <td className="px-3 py-2">{item.effective_state}</td>
                  <td className="px-3 py-2">{item.region ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </main>
  );
}
