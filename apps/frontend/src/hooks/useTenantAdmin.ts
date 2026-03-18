"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/lib/api/client";
import { getStoredScope } from "@/lib/scope";

export interface TenantWorkspaceItem {
  tenant_id: string;
  workspace: string;
  display_name: string | null;
  provider: string;
  scope_kind: string;
  scope_native_id: string | null;
  environment: string | null;
  status: string;
  created_by: string | null;
  updated_by: string | null;
  registered_at: string | null;
  activated_at: string | null;
  archived_at: string | null;
  updated_at: string | null;
}

export interface TenantRoleBindingItem {
  tenant_id: string;
  workspace: string;
  user_id: string;
  role_id: string;
  source_workspace: string;
  applies_to_future_workspaces: boolean;
  granted_by: string | null;
  granted_at: string | null;
  updated_at: string | null;
}

export interface TenantWorkspacesResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  total: number;
  items: TenantWorkspaceItem[];
}

export interface TenantRoleBindingsResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  total: number;
  items: TenantRoleBindingItem[];
}

export interface TenantAdminAuditItem {
  id: number;
  tenant_id: string;
  workspace: string;
  entity_type: string;
  entity_id: string;
  event_type: string;
  event_category: string;
  previous_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  actor_id: string | null;
  actor_email: string | null;
  actor_name: string | null;
  source: string | null;
  correlation_id: string | null;
  created_at: string | null;
}

export interface TenantAdminAuditResponse {
  ok: true;
  tenant_id: string;
  workspace: string;
  limit: number;
  offset: number;
  total: number;
  items: TenantAdminAuditItem[];
}

interface WorkspaceEntryResponse {
  ok: true;
  workspace_entry: TenantWorkspaceItem;
}

interface RoleBindingResponse {
  ok: true;
  binding: TenantRoleBindingItem;
}

export interface TenantWorkspaceUpsertPayload {
  tenant_id: string;
  workspace: string;
  target_workspace?: string;
  display_name?: string;
  provider?: string;
  scope_kind?: string;
  scope_native_id?: string;
  environment?: string;
  status?: string;
  created_by?: string;
  updated_by?: string;
}

export interface TenantRoleBindingPayload {
  tenant_id: string;
  workspace: string;
  role_id: string;
  source_workspace?: string;
  granted_by?: string;
  applies_to_future_workspaces?: boolean;
}

function workspacesQueryKey(scope: { tenantId?: string; workspace?: string }) {
  return ["tenant-admin", "workspaces", scope.tenantId, scope.workspace] as const;
}

function bindingsQueryKey(scope: { tenantId?: string; workspace?: string }) {
  return ["tenant-admin", "role-bindings", scope.tenantId, scope.workspace] as const;
}

function auditQueryKey(
  scope: { tenantId?: string; workspace?: string },
  options: { limit: number; offset: number },
) {
  return ["tenant-admin", "audit", scope.tenantId, scope.workspace, options.limit, options.offset] as const;
}

/**
 * Query registered tenant workspaces.
 */
export function useTenantWorkspaces(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: workspacesQueryKey({ tenantId: scope?.tenantId, workspace: scope?.workspace }),
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    queryFn: async () => {
      return apiClient.get<TenantWorkspacesResponse>("/tenant-admin/workspaces");
    },
  });
}

/**
 * Query tenant-level future-workspace role bindings.
 */
export function useTenantRoleBindings(enabled = true) {
  const scope = getStoredScope();

  return useQuery({
    queryKey: bindingsQueryKey({ tenantId: scope?.tenantId, workspace: scope?.workspace }),
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    queryFn: async () => {
      return apiClient.get<TenantRoleBindingsResponse>("/tenant-admin/role-bindings");
    },
  });
}

/**
 * Query tenant administration audit history.
 */
export function useTenantAdminAudit(
  options: { limit?: number; offset?: number } = {},
  enabled = true,
) {
  const scope = getStoredScope();
  const limit = options.limit ?? 50;
  const offset = options.offset ?? 0;

  return useQuery({
    queryKey: auditQueryKey(
      { tenantId: scope?.tenantId, workspace: scope?.workspace },
      { limit, offset },
    ),
    enabled: Boolean(scope?.tenantId && scope?.workspace && enabled),
    queryFn: async () => {
      return apiClient.get<TenantAdminAuditResponse>("/tenant-admin/audit", {
        query: { limit, offset },
      });
    },
  });
}

/**
 * Mutations for tenant admin workspace and binding management.
 */
export function useTenantAdminMutations() {
  const queryClient = useQueryClient();
  const scope = getStoredScope();

  const invalidateAll = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["tenant-admin", scope?.tenantId, scope?.workspace],
      exact: false,
    });
  };

  const upsertWorkspace = useMutation({
    mutationFn: async (payload: TenantWorkspaceUpsertPayload) => {
      const targetWorkspace = payload.target_workspace?.trim() || payload.workspace;
      if (targetWorkspace === payload.workspace && !payload.target_workspace) {
        return apiClient.post<WorkspaceEntryResponse>("/tenant-admin/workspaces", {
          ...payload,
          target_workspace: targetWorkspace,
        });
      }
      return apiClient.put<WorkspaceEntryResponse>(
        `/tenant-admin/workspaces/${encodeURIComponent(targetWorkspace)}`,
        payload,
      );
    },
    onSuccess: invalidateAll,
  });

  const upsertRoleBinding = useMutation({
    mutationFn: async (params: { userId: string; payload: TenantRoleBindingPayload }) => {
      return apiClient.put<RoleBindingResponse>(
        `/tenant-admin/users/${encodeURIComponent(params.userId)}/role-binding`,
        params.payload,
      );
    },
    onSuccess: invalidateAll,
  });

  const deleteRoleBinding = useMutation({
    mutationFn: async (params: { userId: string }) => {
      return apiClient.del<{ ok: true; deleted: boolean }>(
        `/tenant-admin/users/${encodeURIComponent(params.userId)}/role-binding`,
      );
    },
    onSuccess: invalidateAll,
  });

  return {
    upsertWorkspace,
    upsertRoleBinding,
    deleteRoleBinding,
  };
}
