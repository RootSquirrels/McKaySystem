"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/hooks/useAuth";
import {
  useTenantAdminAudit,
  TenantRoleBindingItem,
  TenantWorkspaceItem,
  useTenantAdminMutations,
  useTenantRoleBindings,
  useTenantWorkspaces,
} from "@/hooks/useTenantAdmin";
import { useUsers } from "@/hooks/useUsersAdmin";
import { ApiError } from "@/lib/api/client";
import { formatUtcDateTime } from "@/lib/dates";
import { getStoredScope } from "@/lib/scope";

function apiErrorMessage(prefix: string, error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code})` : "";
    return `${prefix} [${error.status}${code}]: ${error.message}`;
  }
  if (error instanceof Error) {
    return `${prefix}: ${error.message}`;
  }
  return prefix;
}

function statusBadgeClass(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === "active") {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (normalized === "suspended") {
    return "border-amber-300 bg-amber-50 text-amber-800";
  }
  return "border-rose-300 bg-rose-50 text-rose-700";
}

function assignmentBadgeClass(source: string | null): string {
  const normalized = (source ?? "").trim().toLowerCase();
  if (normalized === "inherited") {
    return "border-violet-300 bg-violet-50 text-violet-800";
  }
  if (normalized === "direct") {
    return "border-cyan-300 bg-cyan-50 text-cyan-800";
  }
  return "border-zinc-300 bg-zinc-100 text-zinc-700";
}

function workspaceDefaults(scope: { workspace: string }, email: string | null) {
  return {
    targetWorkspace: "",
    displayName: "",
    provider: "aws",
    scopeKind: "account",
    scopeNativeId: "",
    environment: "",
    status: "active",
    updatedBy: email ?? "",
    createdBy: email ?? "",
  };
}

function bindingDefaults(scope: { workspace: string }, email: string | null) {
  return {
    userId: "",
    roleId: "viewer",
    sourceWorkspace: scope.workspace,
    grantedBy: email ?? "",
    appliesToFutureWorkspaces: true,
  };
}

export function TenantAdminClientPage() {
  const router = useRouter();
  const scope = getStoredScope();
  const auth = useAuth();
  const permissions = useMemo(() => new Set(auth.user?.permissions ?? []), [auth.user?.permissions]);
  const isAdminFull = permissions.has("admin:full");
  const [auditEventCategory, setAuditEventCategory] = useState("");
  const [auditEntityType, setAuditEntityType] = useState("");
  const [auditTargetWorkspace, setAuditTargetWorkspace] = useState("");
  const [auditQuery, setAuditQuery] = useState("");
  const [auditOffset, setAuditOffset] = useState(0);

  const workspaces = useTenantWorkspaces(isAdminFull);
  const bindings = useTenantRoleBindings(isAdminFull);
  const audit = useTenantAdminAudit(
    {
      limit: 20,
      offset: auditOffset,
      eventCategory: auditEventCategory,
      entityType: auditEntityType,
      targetWorkspace: auditTargetWorkspace,
      q: auditQuery,
    },
    isAdminFull,
  );
  const users = useUsers({ limit: 50, offset: 0, includeInactive: true, enabled: isAdminFull });
  const mutations = useTenantAdminMutations();

  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceFeedback, setWorkspaceFeedback] = useState<string | null>(null);
  const [bindingError, setBindingError] = useState<string | null>(null);
  const [bindingFeedback, setBindingFeedback] = useState<string | null>(null);
  const [editingWorkspace, setEditingWorkspace] = useState<string | null>(null);
  const [workspaceForm, setWorkspaceForm] = useState(() =>
    workspaceDefaults({ workspace: scope?.workspace ?? "" }, auth.user?.email ?? null),
  );
  const [bindingForm, setBindingForm] = useState(() =>
    bindingDefaults({ workspace: scope?.workspace ?? "" }, auth.user?.email ?? null),
  );

  useEffect(() => {
    if (!scope) {
      router.replace("/login");
      return;
    }
    if (!auth.isLoading && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.isAuthenticated, auth.isLoading, router, scope]);

  useEffect(() => {
    if (!scope) {
      return;
    }
    if (!editingWorkspace) {
      setWorkspaceForm(workspaceDefaults(scope, auth.user?.email ?? null));
    }
    setBindingForm((current) => ({
      ...current,
      sourceWorkspace: current.sourceWorkspace || scope.workspace,
      grantedBy: current.grantedBy || auth.user?.email || "",
    }));
  }, [auth.user?.email, editingWorkspace, scope]);

  if (!scope) {
    return null;
  }
  const activeScope = scope;

  const workspaceItems = workspaces.data?.items ?? [];
  const bindingItems = bindings.data?.items ?? [];
  const auditItems = audit.data?.items ?? [];
  const userItems = users.data?.items ?? [];
  const activeCount = workspaceItems.filter((item) => item.status === "active").length;
  const suspendedCount = workspaceItems.filter((item) => item.status === "suspended").length;
  const archivedCount = workspaceItems.filter((item) => item.status === "archived").length;
  const auditLimit = audit.data?.limit ?? 20;
  const auditTotal = audit.data?.total ?? 0;
  const auditPage = Math.floor(auditOffset / auditLimit) + 1;
  const auditTotalPages = Math.max(1, Math.ceil(auditTotal / auditLimit));
  const auditCanPrev = auditOffset > 0;
  const auditCanNext = auditOffset + auditLimit < auditTotal;

  useEffect(() => {
    setAuditOffset(0);
  }, [auditEventCategory, auditEntityType, auditTargetWorkspace, auditQuery]);

  function beginEditWorkspace(item: TenantWorkspaceItem) {
    setEditingWorkspace(item.workspace);
    setWorkspaceForm({
      targetWorkspace: item.workspace,
      displayName: item.display_name ?? "",
      provider: item.provider || "aws",
      scopeKind: item.scope_kind || "account",
      scopeNativeId: item.scope_native_id ?? "",
      environment: item.environment ?? "",
      status: item.status || "active",
      updatedBy: auth.user?.email ?? "",
      createdBy: item.created_by ?? auth.user?.email ?? "",
    });
    setWorkspaceError(null);
    setWorkspaceFeedback(null);
  }

  function resetWorkspaceForm() {
    setEditingWorkspace(null);
    setWorkspaceForm(workspaceDefaults(activeScope, auth.user?.email ?? null));
    setWorkspaceError(null);
    setWorkspaceFeedback(null);
  }

  async function saveWorkspaceWithLifecycleOverride(
    payload: Parameters<typeof mutations.upsertWorkspace.mutateAsync>[0],
    options: { conflictPrompt: string; archiveMigrationPrompt?: string },
  ) {
    try {
      return await mutations.upsertWorkspace.mutateAsync(payload);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const errorCode = error.code ?? "";
        const needsArchiveMigration =
          errorCode === "conflict" &&
          error.message.includes("requires migrating inherited tenant access");
        if (needsArchiveMigration && options.archiveMigrationPrompt) {
          const targetWorkspace = window.prompt(options.archiveMigrationPrompt, "");
          if (!targetWorkspace || !targetWorkspace.trim()) {
            throw error;
          }
          return mutations.upsertWorkspace.mutateAsync({
            ...payload,
            force_lifecycle_change: true,
            migrate_inherited_access_to_workspace: targetWorkspace.trim(),
          });
        }
        const confirmed = window.confirm(`${error.message}\n\n${options.conflictPrompt}`);
        if (!confirmed) {
          throw error;
        }
        return mutations.upsertWorkspace.mutateAsync({
          ...payload,
          force_lifecycle_change: true,
        });
      }
      throw error;
    }
  }

  async function submitWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isAdminFull) {
      return;
    }
    const targetWorkspace = workspaceForm.targetWorkspace.trim();
    if (!targetWorkspace) {
      setWorkspaceError("Workspace identifier is required.");
      return;
    }
    setWorkspaceError(null);
    setWorkspaceFeedback(null);
    try {
      await saveWorkspaceWithLifecycleOverride(
        {
        tenant_id: activeScope.tenantId,
        workspace: activeScope.workspace,
        target_workspace: targetWorkspace,
        display_name: workspaceForm.displayName.trim() || undefined,
        provider: workspaceForm.provider.trim() || "unknown",
        scope_kind: workspaceForm.scopeKind.trim() || "unknown",
        scope_native_id: workspaceForm.scopeNativeId.trim() || undefined,
        environment: workspaceForm.environment.trim() || undefined,
        status: workspaceForm.status,
        created_by: workspaceForm.createdBy.trim() || undefined,
        updated_by: workspaceForm.updatedBy.trim() || undefined,
        },
        {
          conflictPrompt:
            "Confirm that you want to force this lifecycle change even though inherited tenant access still depends on this workspace.",
          archiveMigrationPrompt:
            "Enter the active workspace that should become the new inherited-access source before archiving this workspace.",
        },
      );
      const message = editingWorkspace ? "Workspace updated." : "Workspace registered.";
      setEditingWorkspace(null);
      setWorkspaceForm(workspaceDefaults(activeScope, auth.user?.email ?? null));
      setWorkspaceFeedback(message);
    } catch (error) {
      setWorkspaceError(apiErrorMessage("Failed to save workspace", error));
    }
  }

  async function submitBinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isAdminFull) {
      return;
    }
    const userId = bindingForm.userId.trim();
    if (!userId) {
      setBindingError("User ID is required.");
      return;
    }
    const roleId = bindingForm.roleId.trim();
    if (!roleId) {
      setBindingError("Role ID is required.");
      return;
    }
    setBindingError(null);
    setBindingFeedback(null);
    try {
      await mutations.upsertRoleBinding.mutateAsync({
        userId,
        payload: {
          tenant_id: activeScope.tenantId,
          workspace: activeScope.workspace,
          role_id: roleId,
          source_workspace: bindingForm.sourceWorkspace.trim() || activeScope.workspace,
          granted_by: bindingForm.grantedBy.trim() || undefined,
          applies_to_future_workspaces: bindingForm.appliesToFutureWorkspaces,
        },
      });
      setBindingFeedback("Future-workspace binding saved.");
      setBindingForm(bindingDefaults(activeScope, auth.user?.email ?? null));
    } catch (error) {
      setBindingError(apiErrorMessage("Failed to save binding", error));
    }
  }

  async function deleteBinding(item: TenantRoleBindingItem) {
    const confirmed = window.confirm(
      `Delete inherited tenant binding for ${item.user_id} (${item.role_id})?`,
    );
    if (!confirmed) {
      return;
    }
    setBindingError(null);
    setBindingFeedback(null);
    try {
      await mutations.deleteRoleBinding.mutateAsync({ userId: item.user_id });
      setBindingFeedback(`Binding removed for ${item.user_id}.`);
    } catch (error) {
      setBindingError(apiErrorMessage("Failed to delete binding", error));
    }
  }

  async function quickSetWorkspaceStatus(item: TenantWorkspaceItem, status: string) {
    if (!isAdminFull || item.status === status) {
      return;
    }
    const sourcedBindings = bindingItems.filter(
      (binding) => binding.source_workspace === item.workspace,
    ).length;
    const targetWorkspaceMessage =
      item.workspace === activeScope.workspace
        ? " This is also the current anchor workspace for your tenant admin session."
        : "";
    const bindingMessage =
      sourcedBindings > 0
        ? ` ${sourcedBindings} inherited access binding(s) currently use this workspace as their source.`
        : "";
    const actionMessage =
      status === "archived"
        ? `Archive workspace ${item.workspace}?${bindingMessage}${targetWorkspaceMessage}`
        : status === "suspended"
          ? `Suspend workspace ${item.workspace}?${bindingMessage}${targetWorkspaceMessage}`
          : `Activate workspace ${item.workspace}?`;
    const confirmed = window.confirm(actionMessage);
    if (!confirmed) {
      return;
    }
    setWorkspaceError(null);
    setWorkspaceFeedback(null);
    try {
      await saveWorkspaceWithLifecycleOverride(
        {
          tenant_id: activeScope.tenantId,
          workspace: activeScope.workspace,
          target_workspace: item.workspace,
          display_name: item.display_name ?? undefined,
          provider: item.provider,
          scope_kind: item.scope_kind,
          scope_native_id: item.scope_native_id ?? undefined,
          environment: item.environment ?? undefined,
          status,
          created_by: item.created_by ?? undefined,
          updated_by: auth.user?.email ?? undefined,
        },
        {
          conflictPrompt:
            "Confirm that you want to force this lifecycle change and keep moving this workspace even with inherited access dependencies still attached to it.",
          archiveMigrationPrompt:
            "Enter the active workspace that should become the new inherited-access source before archiving this workspace.",
        },
      );
      setWorkspaceFeedback(`Workspace ${item.workspace} marked ${status}.`);
    } catch (error) {
      setWorkspaceError(apiErrorMessage(`Failed to mark workspace ${status}`, error));
    }
  }

  return (
    <main className="finops-shell relative overflow-hidden">
      <div className="finops-orb finops-orb--one" />
      <div className="finops-orb finops-orb--two" />
      <div className="finops-orb finops-orb--three" />

      <div className="relative z-10 mx-auto min-h-screen w-full max-w-7xl px-6 py-6">
        <header className="finops-panel mb-4 flex flex-wrap items-start justify-between gap-3 rounded-2xl p-4">
          <div>
            <p className="inline-flex rounded-full border border-cyan-300/70 bg-cyan-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-700">
              Tenant Administration
            </p>
            <h1 className="font-display mt-2 text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">
              Workspace Registry and Inherited Access
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              Tenant: <span className="font-medium">{scope.tenantId}</span> | Anchor workspace:{" "}
              <span className="font-medium">{activeScope.workspace}</span>
            </p>
            <p className="mt-2 max-w-3xl text-sm text-slate-600">
              Direct workspace assignments still win. Tenant bindings are the inherited fallback and
              the future-workspace policy source.
            </p>
          </div>
          <div className="flex items-center gap-2 self-start">
            <button
              type="button"
              className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
              onClick={() => {
                router.push("/dashboard");
              }}
            >
              Dashboard
            </button>
            <button
              type="button"
              className="finops-toolbar-btn rounded-lg px-3 py-2 text-sm font-medium transition"
              onClick={() => {
                router.push("/users");
              }}
            >
              Users
            </button>
            <button
              type="button"
              className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700 transition hover:border-rose-400 hover:bg-rose-100"
              onClick={async () => {
                await auth.logout();
                router.push("/login");
              }}
            >
              Logout
            </button>
          </div>
        </header>

        {!isAdminFull ? (
          <section className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
            This console requires <code>admin:full</code>.
          </section>
        ) : null}

        {isAdminFull ? (
          <>
            <section className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Workspaces</p>
                <p className="font-display mt-1 text-2xl font-semibold text-white">{workspaceItems.length}</p>
              </article>
              <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Active</p>
                <p className="font-display mt-1 text-2xl font-semibold text-white">{activeCount}</p>
              </article>
              <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Suspended / Archived</p>
                <p className="font-display mt-1 text-2xl font-semibold text-white">
                  {suspendedCount} / {archivedCount}
                </p>
              </article>
              <article className="rounded-xl border border-cyan-300/35 bg-slate-900/45 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-100/85">Future Bindings</p>
                <p className="font-display mt-1 text-2xl font-semibold text-white">{bindingItems.length}</p>
              </article>
            </section>

            <section className="mb-4 grid gap-4 xl:grid-cols-[1.3fr_0.9fr]">
              <div className="finops-panel rounded-2xl p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                    Workspace Registry
                  </h2>
                  <button
                    type="button"
                    className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-700 transition hover:bg-slate-100"
                    onClick={resetWorkspaceForm}
                  >
                    {editingWorkspace ? "New Workspace" : "Reset"}
                  </button>
                </div>

                {workspaces.isLoading ? (
                  <p className="text-sm text-slate-600">Loading workspaces...</p>
                ) : null}
                {workspaces.error ? (
                  <p className="rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
                    {apiErrorMessage("Failed to load workspaces", workspaces.error)}
                  </p>
                ) : null}

                <div className="overflow-x-auto rounded-xl border border-slate-200">
                  <table className="min-w-full text-left text-sm text-slate-700">
                    <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                      <tr>
                        <th className="px-3 py-2">Workspace</th>
                        <th className="px-3 py-2">Scope</th>
                        <th className="px-3 py-2">Environment</th>
                        <th className="px-3 py-2">Status</th>
                        <th className="px-3 py-2">Updated</th>
                        <th className="px-3 py-2">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {workspaceItems.map((item) => (
                        <tr key={item.workspace} className="border-t border-slate-100">
                          <td className="px-3 py-2">
                            <div className="font-medium text-slate-900">{item.workspace}</div>
                            <div className="text-xs text-slate-500">{item.display_name ?? "-"}</div>
                          </td>
                          <td className="px-3 py-2">
                            {item.provider}:{item.scope_kind}
                            <div className="text-xs text-slate-500">{item.scope_native_id ?? "-"}</div>
                          </td>
                          <td className="px-3 py-2">{item.environment ?? "-"}</td>
                          <td className="px-3 py-2">
                            <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${statusBadgeClass(item.status)}`}>
                              {item.status}
                            </span>
                          </td>
                          <td className="px-3 py-2">{formatUtcDateTime(item.updated_at)}</td>
                          <td className="px-3 py-2">
                            <button
                              type="button"
                              className="rounded-lg border border-cyan-300 bg-cyan-50 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-800 transition hover:bg-cyan-100"
                              onClick={() => beginEditWorkspace(item)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="ml-2 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-slate-700 transition hover:bg-slate-100"
                              onClick={() => {
                                void quickSetWorkspaceStatus(
                                  item,
                                  item.status === "active" ? "suspended" : "active",
                                );
                              }}
                            >
                              {item.status === "active" ? "Suspend" : "Activate"}
                            </button>
                            {item.status !== "archived" ? (
                              <button
                                type="button"
                                className="ml-2 rounded-lg border border-rose-300 bg-rose-50 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-rose-700 transition hover:bg-rose-100"
                                onClick={() => {
                                  void quickSetWorkspaceStatus(item, "archived");
                                }}
                              >
                                Archive
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <form className="finops-panel rounded-2xl p-4" onSubmit={submitWorkspace}>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                  {editingWorkspace ? "Edit Workspace" : "Register Workspace"}
                </h2>
                <div className="mt-3 grid gap-3">
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Workspace ID
                    </span>
                    <input
                      value={workspaceForm.targetWorkspace}
                      onChange={(event) => setWorkspaceForm((current) => ({ ...current, targetWorkspace: event.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      disabled={Boolean(editingWorkspace)}
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Display Name
                    </span>
                    <input
                      value={workspaceForm.displayName}
                      onChange={(event) => setWorkspaceForm((current) => ({ ...current, displayName: event.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    />
                  </label>
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block text-sm">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                        Provider
                      </span>
                      <input
                        value={workspaceForm.provider}
                        onChange={(event) => setWorkspaceForm((current) => ({ ...current, provider: event.target.value }))}
                        className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                        Scope Kind
                      </span>
                      <input
                        value={workspaceForm.scopeKind}
                        onChange={(event) => setWorkspaceForm((current) => ({ ...current, scopeKind: event.target.value }))}
                        className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      />
                    </label>
                  </div>
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Native Scope ID
                    </span>
                    <input
                      value={workspaceForm.scopeNativeId}
                      onChange={(event) => setWorkspaceForm((current) => ({ ...current, scopeNativeId: event.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    />
                  </label>
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block text-sm">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                        Environment
                      </span>
                      <input
                        value={workspaceForm.environment}
                        onChange={(event) => setWorkspaceForm((current) => ({ ...current, environment: event.target.value }))}
                        className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                        Status
                      </span>
                      <select
                        value={workspaceForm.status}
                        onChange={(event) => setWorkspaceForm((current) => ({ ...current, status: event.target.value }))}
                        className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      >
                        <option value="active">active</option>
                        <option value="suspended">suspended</option>
                        <option value="archived">archived</option>
                      </select>
                    </label>
                  </div>
                  {workspaceError ? (
                    <p className="rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
                      {workspaceError}
                    </p>
                  ) : null}
                  {workspaceFeedback ? (
                    <p className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-700">
                      {workspaceFeedback}
                    </p>
                  ) : null}
                  <div className="flex items-center justify-end gap-2">
                    {editingWorkspace ? (
                      <button
                        type="button"
                        className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-700 transition hover:bg-slate-100"
                        onClick={resetWorkspaceForm}
                      >
                        Cancel
                      </button>
                    ) : null}
                    <button
                      type="submit"
                      disabled={mutations.upsertWorkspace.isPending}
                      className="rounded-lg border border-cyan-300 bg-cyan-50 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-cyan-800 transition hover:bg-cyan-100 disabled:opacity-50"
                    >
                      {mutations.upsertWorkspace.isPending
                        ? "Saving..."
                        : editingWorkspace
                          ? "Save Workspace"
                          : "Register Workspace"}
                    </button>
                  </div>
                </div>
              </form>
            </section>

            <section className="grid gap-4 xl:grid-cols-[1.2fr_0.95fr]">
              <div className="finops-panel rounded-2xl p-4">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                  Future-Workspace Access Policies
                </h2>
                <p className="mt-2 text-sm text-slate-600">
                  These bindings are used as inherited fallback at runtime and are auto-applied when
                  new workspaces are bootstrapped.
                </p>

                {bindings.isLoading ? (
                  <p className="mt-3 text-sm text-slate-600">Loading bindings...</p>
                ) : null}
                {bindings.error ? (
                  <p className="mt-3 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
                    {apiErrorMessage("Failed to load bindings", bindings.error)}
                  </p>
                ) : null}

                <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
                  <table className="min-w-full text-left text-sm text-slate-700">
                    <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                      <tr>
                        <th className="px-3 py-2">User</th>
                        <th className="px-3 py-2">Role</th>
                        <th className="px-3 py-2">Source Workspace</th>
                        <th className="px-3 py-2">Future</th>
                        <th className="px-3 py-2">Updated</th>
                        <th className="px-3 py-2">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bindingItems.map((item) => (
                        <tr key={`${item.user_id}:${item.role_id}`} className="border-t border-slate-100">
                          <td className="px-3 py-2">{item.user_id}</td>
                          <td className="px-3 py-2">{item.role_id}</td>
                          <td className="px-3 py-2">{item.source_workspace}</td>
                          <td className="px-3 py-2">{item.applies_to_future_workspaces ? "Yes" : "No"}</td>
                          <td className="px-3 py-2">{formatUtcDateTime(item.updated_at)}</td>
                          <td className="px-3 py-2">
                            <button
                              type="button"
                              className="rounded-lg border border-rose-300 bg-rose-50 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-rose-700 transition hover:bg-rose-100"
                              onClick={() => {
                                void deleteBinding(item);
                              }}
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <form className="finops-panel rounded-2xl p-4" onSubmit={submitBinding}>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                  Add or Update Binding
                </h2>
                <div className="mt-3 grid gap-3">
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      User ID
                    </span>
                    <input
                      value={bindingForm.userId}
                      onChange={(event) => setBindingForm((current) => ({ ...current, userId: event.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    />
                  </label>
                  <div className="grid gap-3 md:grid-cols-2">
                    <label className="block text-sm">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                        Role ID
                      </span>
                      <input
                        value={bindingForm.roleId}
                        onChange={(event) => setBindingForm((current) => ({ ...current, roleId: event.target.value }))}
                        className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                        Source Workspace
                      </span>
                      <input
                        value={bindingForm.sourceWorkspace}
                        onChange={(event) => setBindingForm((current) => ({ ...current, sourceWorkspace: event.target.value }))}
                        className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                      />
                    </label>
                  </div>
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Granted By
                    </span>
                    <input
                      value={bindingForm.grantedBy}
                      onChange={(event) => setBindingForm((current) => ({ ...current, grantedBy: event.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    />
                  </label>
                  <label className="flex items-center text-sm">
                    <input
                      type="checkbox"
                      checked={bindingForm.appliesToFutureWorkspaces}
                      onChange={(event) =>
                        setBindingForm((current) => ({
                          ...current,
                          appliesToFutureWorkspaces: event.target.checked,
                        }))
                      }
                    />
                    <span className="ml-2 text-slate-700">Applies to future workspaces</span>
                  </label>
                  {bindingError ? (
                    <p className="rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
                      {bindingError}
                    </p>
                  ) : null}
                  {bindingFeedback ? (
                    <p className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-700">
                      {bindingFeedback}
                    </p>
                  ) : null}
                  <div className="flex items-center justify-end">
                    <button
                      type="submit"
                      disabled={mutations.upsertRoleBinding.isPending}
                      className="rounded-lg border border-cyan-300 bg-cyan-50 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-cyan-800 transition hover:bg-cyan-100 disabled:opacity-50"
                    >
                      {mutations.upsertRoleBinding.isPending ? "Saving..." : "Save Binding"}
                    </button>
                  </div>
                </div>
              </form>
            </section>

            <section className="mt-4 grid gap-4 xl:grid-cols-[1.15fr_0.95fr]">
              <div className="finops-panel rounded-2xl p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                      Current Workspace Access Snapshot
                    </h2>
                    <p className="mt-1 text-sm text-slate-600">
                      Effective access in <span className="font-medium">{activeScope.workspace}</span>,
                      including inherited tenant bindings.
                    </p>
                  </div>
                  <span className="rounded-full border border-slate-300 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-700">
                    {userItems.length} users
                  </span>
                </div>

                {users.isLoading ? (
                  <p className="mt-3 text-sm text-slate-600">Loading effective access...</p>
                ) : null}
                {users.error ? (
                  <p className="mt-3 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
                    {apiErrorMessage("Failed to load effective access", users.error)}
                  </p>
                ) : null}

                <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
                  <table className="min-w-full text-left text-sm text-slate-700">
                    <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                      <tr>
                        <th className="px-3 py-2">User</th>
                        <th className="px-3 py-2">Effective Role</th>
                        <th className="px-3 py-2">Assignment</th>
                        <th className="px-3 py-2">Source Workspace</th>
                        <th className="px-3 py-2">State</th>
                      </tr>
                    </thead>
                    <tbody>
                      {userItems.length ? (
                        userItems.map((item) => (
                          <tr key={item.user_id} className="border-t border-slate-100">
                            <td className="px-3 py-2">
                              <div className="font-medium text-slate-900">{item.email}</div>
                              <div className="text-xs text-slate-500">
                                {item.user_id}
                                {item.full_name ? ` · ${item.full_name}` : ""}
                              </div>
                            </td>
                            <td className="px-3 py-2">
                              <div className="font-medium text-slate-900">{item.role_name ?? item.role_id ?? "-"}</div>
                              <div className="text-xs text-slate-500">{item.role_id ?? "no role"}</div>
                            </td>
                            <td className="px-3 py-2">
                              <span
                                className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${assignmentBadgeClass(
                                  item.assignment_source,
                                )}`}
                              >
                                {item.assignment_source ?? "unknown"}
                              </span>
                            </td>
                            <td className="px-3 py-2">{item.source_workspace ?? activeScope.workspace}</td>
                            <td className="px-3 py-2">
                              <span
                                className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${
                                  item.is_active
                                    ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                                    : "border-zinc-300 bg-zinc-100 text-zinc-700"
                                }`}
                              >
                                {item.is_active ? "active" : "inactive"}
                              </span>
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={5} className="px-3 py-6 text-center text-sm text-slate-500">
                            No users found for this workspace snapshot.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="finops-panel rounded-2xl p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">
                      Tenant Admin Audit History
                    </h2>
                    <p className="mt-1 text-sm text-slate-600">
                      Recent workspace and inherited-access administration changes for this tenant.
                    </p>
                  </div>
                  <span className="rounded-full border border-slate-300 bg-slate-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-700">
                    {audit.data?.total ?? 0} total
                  </span>
                </div>

                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Search
                    </span>
                    <input
                      value={auditQuery}
                      onChange={(event) => setAuditQuery(event.target.value)}
                      placeholder="workspace, actor, entity, event"
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Workspace
                    </span>
                    <input
                      value={auditTargetWorkspace}
                      onChange={(event) => setAuditTargetWorkspace(event.target.value)}
                      placeholder="prod"
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    />
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Event Category
                    </span>
                    <select
                      value={auditEventCategory}
                      onChange={(event) => setAuditEventCategory(event.target.value)}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    >
                      <option value="">all</option>
                      <option value="tenant_admin">tenant_admin</option>
                      <option value="rbac">rbac</option>
                    </select>
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">
                      Entity Type
                    </span>
                    <select
                      value={auditEntityType}
                      onChange={(event) => setAuditEntityType(event.target.value)}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-200"
                    >
                      <option value="">all</option>
                      <option value="tenant_workspace">tenant_workspace</option>
                      <option value="tenant_role_binding">tenant_role_binding</option>
                      <option value="user_role_assignment">user_role_assignment</option>
                    </select>
                  </label>
                </div>

                {audit.isLoading ? (
                  <p className="mt-3 text-sm text-slate-600">Loading audit history...</p>
                ) : null}
                {audit.error ? (
                  <p className="mt-3 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-700">
                    {apiErrorMessage("Failed to load audit history", audit.error)}
                  </p>
                ) : null}

                <div className="mt-3 flex items-center justify-between text-sm">
                  <p className="text-slate-600">
                    Showing {auditTotal === 0 ? 0 : auditOffset + 1}-
                    {Math.min(auditOffset + auditItems.length, auditTotal)} of {auditTotal}
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-50"
                      disabled={!auditCanPrev}
                      onClick={() => {
                        setAuditOffset((current) => Math.max(0, current - auditLimit));
                      }}
                    >
                      Previous
                    </button>
                    <span className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700">
                      Page {auditPage} / {auditTotalPages}
                    </span>
                    <button
                      type="button"
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-50"
                      disabled={!auditCanNext}
                      onClick={() => {
                        setAuditOffset((current) => current + auditLimit);
                      }}
                    >
                      Next
                    </button>
                  </div>
                </div>

                <div className="mt-3 space-y-3">
                  {auditItems.length ? (
                    auditItems.map((item) => (
                      <article
                        key={item.id}
                        className="rounded-xl border border-slate-200 bg-white/80 p-3"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm font-semibold text-slate-900">{item.event_type}</p>
                          <p className="text-xs text-slate-500">{formatUtcDateTime(item.created_at)}</p>
                        </div>
                        <p className="mt-1 text-xs uppercase tracking-wide text-slate-500">
                          {item.entity_type}:{item.entity_id}
                        </p>
                        <p className="mt-2 text-sm text-slate-600">
                          Actor:{" "}
                          <span className="font-medium text-slate-800">
                            {item.actor_email ?? item.actor_id ?? "unknown"}
                          </span>
                        </p>
                        <p className="mt-1 text-sm text-slate-600">
                          Workspace: <span className="font-medium text-slate-800">{item.workspace}</span>
                        </p>
                      </article>
                    ))
                  ) : (
                    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-6 text-center text-sm text-slate-500">
                      No tenant administration audit events yet.
                    </div>
                  )}
                </div>
              </div>
            </section>
          </>
        ) : null}
      </div>
    </main>
  );
}
