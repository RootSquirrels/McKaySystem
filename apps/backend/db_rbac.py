"""Database helpers for RBAC data access.

All queries are explicitly scoped by `tenant_id` and `workspace`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apps.backend.db import fetch_all_dict_conn, fetch_one_dict_conn

TENANT_POLICY_WORKSPACE = "__tenant__"


def _dict_from_cursor_row(cursor: Any, row: Sequence[Any] | None) -> dict[str, Any] | None:
    """Build a dict from a cursor row using cursor.description metadata."""
    if row is None:
        return None
    columns = [str(desc[0]) for desc in (getattr(cursor, "description", None) or [])]
    if not columns:
        return None
    return dict(zip(columns, row, strict=False))


# Column-mapped DTOs intentionally mirror table schemas for explicitness.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class UserUpsert:
    """Input payload for idempotent user upsert operations."""

    tenant_id: str
    workspace: str
    user_id: str
    email: str
    password_hash: str | None
    full_name: str | None = None
    external_id: str | None = None
    auth_provider: str = "local"
    is_active: bool = True
    is_superadmin: bool = False


@dataclass(frozen=True)
class ApiKeyUpsert:
    """Input payload for idempotent API key upsert operations."""

    tenant_id: str
    workspace: str
    key_id: str
    key_hash: str
    name: str
    description: str | None = None
    user_id: str | None = None
    key_type: str = "secret"
    expires_at: Any | None = None


@dataclass(frozen=True)
class SessionUpsert:
    """Input payload for idempotent session upsert operations."""

    tenant_id: str
    workspace: str
    session_id: str
    session_token_hash: str
    user_id: str
    expires_at: datetime


@dataclass(frozen=True)
class UserWorkspaceRoleUpsert:
    """Input payload for idempotent user-workspace-role assignment operations."""

    tenant_id: str
    workspace: str
    user_id: str
    role_id: str
    granted_by: str | None = None


@dataclass(frozen=True)
class UserListQuery:
    """Input payload for scoped user list queries."""

    tenant_id: str
    workspace: str
    limit: int = 100
    offset: int = 0
    query: str | None = None
    include_inactive: bool = False


@dataclass(frozen=True)
class TenantWorkspaceUpsert:
    """Input payload for idempotent tenant workspace registry operations."""

    tenant_id: str
    workspace: str
    display_name: str | None = None
    provider: str = "unknown"
    scope_kind: str = "unknown"
    scope_native_id: str | None = None
    environment: str | None = None
    status: str = "active"
    updated_by: str | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class TenantRoleBindingUpsert:
    """Input payload for tenant-level inherited role policy operations."""

    tenant_id: str
    user_id: str
    role_id: str
    source_workspace: str
    granted_by: str | None = None
    applies_to_future_workspaces: bool = True


# pylint: enable=too-many-instance-attributes


def get_user_by_email(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    email: str,
) -> dict[str, Any] | None:
    """Return one user row by scoped email."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          tenant_id,
          workspace,
          user_id,
          email,
          password_hash,
          full_name,
          external_id,
          auth_provider,
          is_active,
          is_superadmin,
          last_login_at,
          created_at,
          updated_at
        FROM users
        WHERE tenant_id = %s
          AND workspace = %s
          AND email = %s
        """,
        (tenant_id, workspace, email),
    )


def get_inherited_user_by_email(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    email: str,
) -> dict[str, Any] | None:
    """Return an inherited user row for a target workspace by email."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          %s AS workspace,
          u.user_id,
          u.email,
          u.password_hash,
          u.full_name,
          u.external_id,
          u.auth_provider,
          u.is_active,
          u.is_superadmin,
          u.last_login_at,
          u.created_at,
          u.updated_at,
          trb.role_id,
          trb.source_workspace,
          'inherited' AS assignment_source
        FROM tenant_role_bindings trb
        JOIN users u
          ON u.tenant_id = trb.tenant_id
         AND u.workspace = trb.source_workspace
         AND u.user_id = trb.user_id
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND trb.applies_to_future_workspaces = TRUE
          AND u.email = %s
          AND u.is_active = TRUE
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces tw
              WHERE tw.tenant_id = %s
                AND tw.workspace = %s
                AND tw.status <> 'archived'
            )
            OR EXISTS (
              SELECT 1
              FROM roles r
              WHERE r.tenant_id = %s
                AND r.workspace = %s
            )
          )
        ORDER BY trb.user_id ASC
        LIMIT 1
        """,
        (
            workspace,
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            email,
            tenant_id,
            workspace,
            tenant_id,
            workspace,
        ),
    )


def get_user_by_id(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return one user row by scoped user identifier."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          tenant_id,
          workspace,
          user_id,
          email,
          password_hash,
          full_name,
          external_id,
          auth_provider,
          is_active,
          is_superadmin,
          last_login_at,
          created_at,
          updated_at
        FROM users
        WHERE tenant_id = %s
          AND workspace = %s
          AND user_id = %s
        """,
        (tenant_id, workspace, user_id),
    )


def get_effective_workspace_role(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return the effective role mapping for a user in one workspace."""
    direct = fetch_one_dict_conn(
        conn,
        """
        SELECT
          uwr.tenant_id,
          uwr.workspace,
          uwr.user_id,
          uwr.role_id,
          uwr.granted_by,
          uwr.granted_at,
          uwr.workspace AS source_workspace,
          'direct' AS assignment_source
        FROM user_workspace_roles uwr
        JOIN users u
          ON u.tenant_id = uwr.tenant_id
         AND u.workspace = uwr.workspace
         AND u.user_id = uwr.user_id
        WHERE uwr.tenant_id = %s
          AND uwr.workspace = %s
          AND uwr.user_id = %s
          AND u.is_active = TRUE
        """,
        (tenant_id, workspace, user_id),
    )
    if direct is not None:
        return direct

    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          %s AS workspace,
          trb.user_id,
          trb.role_id,
          trb.granted_by,
          trb.granted_at,
          trb.source_workspace,
          'inherited' AS assignment_source
        FROM tenant_role_bindings trb
        JOIN users u
          ON u.tenant_id = trb.tenant_id
         AND u.workspace = trb.source_workspace
         AND u.user_id = trb.user_id
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND trb.user_id = %s
          AND trb.applies_to_future_workspaces = TRUE
          AND u.is_active = TRUE
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces tw
              WHERE tw.tenant_id = %s
                AND tw.workspace = %s
                AND tw.status <> 'archived'
            )
            OR EXISTS (
              SELECT 1
              FROM roles r
              WHERE r.tenant_id = %s
                AND r.workspace = %s
            )
          )
        LIMIT 1
        """,
        (
            workspace,
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            user_id,
            tenant_id,
            workspace,
            tenant_id,
            workspace,
        ),
    )


def create_user(conn: Any, *, user: UserUpsert) -> dict[str, Any] | None:
    """Create or update a scoped user row in an idempotent way."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (
              tenant_id,
              workspace,
              user_id,
              email,
              password_hash,
              full_name,
              external_id,
              auth_provider,
              is_active,
              is_superadmin,
              updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, workspace, user_id)
            DO UPDATE SET
              email = EXCLUDED.email,
              password_hash = EXCLUDED.password_hash,
              full_name = EXCLUDED.full_name,
              external_id = EXCLUDED.external_id,
              auth_provider = EXCLUDED.auth_provider,
              is_active = EXCLUDED.is_active,
              is_superadmin = EXCLUDED.is_superadmin,
              updated_at = now()
            RETURNING
              tenant_id,
              workspace,
              user_id,
              email,
              password_hash,
              full_name,
              external_id,
              auth_provider,
              is_active,
              is_superadmin,
              last_login_at,
              created_at,
              updated_at
            """,
            (
                user.tenant_id,
                user.workspace,
                user.user_id,
                user.email,
                user.password_hash,
                user.full_name,
                user.external_id,
                user.auth_provider,
                user.is_active,
                user.is_superadmin,
            ),
        )
        return _dict_from_cursor_row(cur, cur.fetchone())


def list_users_page(conn: Any, *, query: UserListQuery) -> tuple[list[dict[str, Any]], int]:
    """List users with deterministic paging and scoped total count."""
    where = ["u.tenant_id = %s", "u.workspace = %s"]
    params: list[Any] = [query.tenant_id, query.workspace]

    if not query.include_inactive:
        where.append("u.is_active = TRUE")
    if query.query:
        where.append(
            "(u.user_id ILIKE %s OR u.email ILIKE %s OR COALESCE(u.full_name, '') ILIKE %s)"
        )
        pattern = f"%{query.query}%"
        params.extend([pattern, pattern, pattern])

    sql_items = f"""
        WITH base_users AS (
          SELECT
            u.tenant_id,
            u.workspace,
            u.user_id,
            u.email,
            u.full_name,
            u.external_id,
            u.auth_provider,
            u.is_active,
            u.is_superadmin,
            u.last_login_at,
            u.created_at,
            u.updated_at,
            uwr.role_id AS direct_role_id,
            r_direct.name AS direct_role_name,
            trb.role_id AS inherited_role_id,
            r_inherited.name AS inherited_role_name,
            trb.source_workspace AS inherited_source_workspace
          FROM users u
          LEFT JOIN user_workspace_roles uwr
            ON uwr.tenant_id = u.tenant_id
           AND uwr.workspace = u.workspace
           AND uwr.user_id = u.user_id
          LEFT JOIN roles r_direct
            ON r_direct.tenant_id = uwr.tenant_id
           AND r_direct.workspace = uwr.workspace
           AND r_direct.role_id = uwr.role_id
          LEFT JOIN tenant_role_bindings trb
            ON trb.tenant_id = u.tenant_id
           AND trb.workspace = %s
           AND trb.user_id = u.user_id
           AND trb.applies_to_future_workspaces = TRUE
          LEFT JOIN roles r_inherited
            ON r_inherited.tenant_id = trb.tenant_id
           AND r_inherited.workspace = trb.source_workspace
           AND r_inherited.role_id = trb.role_id
          WHERE {" AND ".join(where)}
        )
        SELECT
          tenant_id,
          workspace,
          user_id,
          email,
          full_name,
          external_id,
          auth_provider,
          is_active,
          is_superadmin,
          last_login_at,
          created_at,
          updated_at,
          COALESCE(direct_role_id, inherited_role_id) AS role_id,
          COALESCE(direct_role_name, inherited_role_name) AS role_name,
          CASE
            WHEN direct_role_id IS NOT NULL THEN 'direct'
            WHEN inherited_role_id IS NOT NULL THEN 'inherited'
            ELSE NULL
          END AS assignment_source,
          CASE
            WHEN direct_role_id IS NOT NULL THEN workspace
            WHEN inherited_role_id IS NOT NULL THEN inherited_source_workspace
            ELSE NULL
          END AS source_workspace
        FROM base_users
        ORDER BY email ASC, user_id ASC
        LIMIT %s OFFSET %s
    """
    sql_count = f"SELECT COUNT(*)::bigint AS n FROM users u WHERE {' AND '.join(where)}"
    rows = fetch_all_dict_conn(
        conn,
        sql_items,
        tuple([TENANT_POLICY_WORKSPACE] + params + [query.limit, query.offset]),
    )
    count_row = fetch_one_dict_conn(conn, sql_count, tuple(params))
    total = int((count_row or {}).get("n") or 0)
    return rows, total


def list_roles(conn: Any, *, tenant_id: str, workspace: str) -> list[dict[str, Any]]:
    """List scoped roles with deterministic ordering and aggregated permissions."""
    rows = fetch_all_dict_conn(
        conn,
        """
        SELECT
          r.tenant_id,
          r.workspace,
          r.role_id,
          r.name,
          r.description,
          r.is_system,
          r.created_at,
          r.updated_at,
          COALESCE(
            ARRAY_AGG(rp.permission_id ORDER BY rp.permission_id)
              FILTER (WHERE rp.permission_id IS NOT NULL),
            ARRAY[]::text[]
          ) AS permissions
        FROM roles r
        LEFT JOIN role_permissions rp
          ON rp.tenant_id = r.tenant_id
         AND rp.workspace = r.workspace
         AND rp.role_id = r.role_id
        WHERE r.tenant_id = %s
          AND r.workspace = %s
        GROUP BY
          r.tenant_id,
          r.workspace,
          r.role_id,
          r.name,
          r.description,
          r.is_system,
          r.created_at,
          r.updated_at
        ORDER BY r.role_id ASC
        """,
        (tenant_id, workspace),
    )
    normalized: list[dict[str, Any]] = []
    for row in rows:
        permissions_raw = row.get("permissions")
        permissions = (
            [str(item) for item in permissions_raw]
            if isinstance(permissions_raw, list)
            else []
        )
        normalized.append(
            {
                **row,
                "permissions": permissions,
            }
        )
    return normalized


def set_user_active(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str,
    is_active: bool,
) -> bool:
    """Set user active status and report whether a row was updated."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET
              is_active = %s,
              updated_at = now()
            WHERE tenant_id = %s
              AND workspace = %s
              AND user_id = %s
            """,
            (is_active, tenant_id, workspace, user_id),
        )
        return bool(cur.rowcount)


def list_api_keys(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str | None = None,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """List API keys for one tenant/workspace scope."""
    where = ["tenant_id = %s", "workspace = %s"]
    params: list[Any] = [tenant_id, workspace]

    if user_id:
        where.append("user_id = %s")
        params.append(user_id)
    if not include_inactive:
        where.append("is_active = TRUE")

    sql = f"""
        SELECT
          tenant_id,
          workspace,
          key_id,
          key_hash,
          key_type,
          name,
          description,
          user_id,
          last_used_at,
          expires_at,
          is_active,
          created_at
        FROM api_keys
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC, key_id ASC
    """
    return fetch_all_dict_conn(conn, sql, tuple(params))


def create_api_key(conn: Any, *, api_key: ApiKeyUpsert) -> dict[str, Any] | None:
    """Create or update an API key row in an idempotent way."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_keys (
              tenant_id,
              workspace,
              key_id,
              key_hash,
              key_type,
              name,
              description,
              user_id,
              expires_at,
              is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (tenant_id, workspace, key_id)
            DO UPDATE SET
              key_hash = EXCLUDED.key_hash,
              key_type = EXCLUDED.key_type,
              name = EXCLUDED.name,
              description = EXCLUDED.description,
              user_id = EXCLUDED.user_id,
              expires_at = EXCLUDED.expires_at,
              is_active = TRUE
            RETURNING
              tenant_id,
              workspace,
              key_id,
              key_hash,
              key_type,
              name,
              description,
              user_id,
              last_used_at,
              expires_at,
              is_active,
              created_at
            """,
            (
                api_key.tenant_id,
                api_key.workspace,
                api_key.key_id,
                api_key.key_hash,
                api_key.key_type,
                api_key.name,
                api_key.description,
                api_key.user_id,
                api_key.expires_at,
            ),
        )
        return _dict_from_cursor_row(cur, cur.fetchone())


def revoke_api_key(conn: Any, *, tenant_id: str, workspace: str, key_id: str) -> bool:
    """Disable one API key and report whether a row was updated."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE api_keys
            SET is_active = FALSE
            WHERE tenant_id = %s
              AND workspace = %s
              AND key_id = %s
              AND is_active = TRUE
            """,
            (tenant_id, workspace, key_id),
        )
        return bool(cur.rowcount)


def get_user_workspace_role(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return one scoped user-workspace-role mapping."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          tenant_id,
          workspace,
          user_id,
          role_id,
          granted_by,
          granted_at
        FROM user_workspace_roles
        WHERE tenant_id = %s
          AND workspace = %s
          AND user_id = %s
        """,
        (tenant_id, workspace, user_id),
    )


def list_tenant_workspaces(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
) -> list[str]:
    """Return discovered workspaces for one tenant anchored to a known scope.

    Args:
        conn: Open database connection.
        tenant_id: Tenant identifier.
        anchor_workspace: One existing workspace used to keep query scope explicit.

    Returns:
        Sorted list of workspace identifiers for the tenant.
    """
    registered = list_registered_tenant_workspaces(
        conn,
        tenant_id=tenant_id,
        anchor_workspace=anchor_workspace,
    )
    if registered:
        return [str(row["workspace"]) for row in registered if row.get("workspace")]

    rows = fetch_all_dict_conn(
        conn,
        """
        SELECT DISTINCT r_all.workspace
        FROM roles r_anchor
        JOIN roles r_all
          ON r_all.tenant_id = r_anchor.tenant_id
        WHERE r_anchor.tenant_id = %s
          AND r_anchor.workspace = %s
        ORDER BY r_all.workspace ASC
        """,
        (tenant_id, anchor_workspace),
    )
    return [str(row["workspace"]) for row in rows if row.get("workspace")]


def list_registered_tenant_workspaces(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
) -> list[dict[str, Any]]:
    """Return registered tenant workspaces anchored to one known workspace."""
    return fetch_all_dict_conn(
        conn,
        """
        SELECT
          tw.tenant_id,
          tw.workspace,
          tw.display_name,
          tw.provider,
          tw.scope_kind,
          tw.scope_native_id,
          tw.environment,
          tw.status,
          tw.created_by,
          tw.updated_by,
          tw.registered_at,
          tw.activated_at,
          tw.archived_at,
          tw.updated_at
        FROM tenant_workspaces tw
        WHERE tw.tenant_id = %s
          AND EXISTS (
            SELECT 1
            FROM tenant_workspaces anchor
            WHERE anchor.tenant_id = %s
              AND anchor.workspace = %s
          )
        ORDER BY tw.workspace ASC
        """,
        (tenant_id, tenant_id, anchor_workspace),
    )


def get_tenant_workspace(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
    target_workspace: str,
) -> dict[str, Any] | None:
    """Return one registered tenant workspace when the anchor scope exists."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          tw.tenant_id,
          tw.workspace,
          tw.display_name,
          tw.provider,
          tw.scope_kind,
          tw.scope_native_id,
          tw.environment,
          tw.status,
          tw.created_by,
          tw.updated_by,
          tw.registered_at,
          tw.activated_at,
          tw.archived_at,
          tw.updated_at
        FROM tenant_workspaces tw
        WHERE tw.tenant_id = %s
          AND tw.workspace = %s
          AND EXISTS (
            SELECT 1
            FROM tenant_workspaces anchor
            WHERE anchor.tenant_id = %s
              AND anchor.workspace = %s
          )
        """,
        (tenant_id, target_workspace, tenant_id, anchor_workspace),
    )


def upsert_tenant_workspace(
    conn: Any,
    *,
    workspace_entry: TenantWorkspaceUpsert,
) -> dict[str, Any] | None:
    """Create or update one tenant workspace registry entry idempotently."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenant_workspaces (
              tenant_id,
              workspace,
              display_name,
              provider,
              scope_kind,
              scope_native_id,
              environment,
              status,
              created_by,
              updated_by,
              registered_at,
              activated_at,
              archived_at,
              updated_at
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
              CASE WHEN %s = 'active' THEN now() ELSE NULL END,
              CASE WHEN %s = 'archived' THEN now() ELSE NULL END,
              now()
            )
            ON CONFLICT (tenant_id, workspace)
            DO UPDATE SET
              display_name = EXCLUDED.display_name,
              provider = EXCLUDED.provider,
              scope_kind = EXCLUDED.scope_kind,
              scope_native_id = EXCLUDED.scope_native_id,
              environment = EXCLUDED.environment,
              status = EXCLUDED.status,
              updated_by = EXCLUDED.updated_by,
              activated_at = CASE
                WHEN EXCLUDED.status = 'active'
                  THEN COALESCE(tenant_workspaces.activated_at, now())
                ELSE tenant_workspaces.activated_at
              END,
              archived_at = CASE
                WHEN EXCLUDED.status = 'archived'
                  THEN COALESCE(tenant_workspaces.archived_at, now())
                WHEN EXCLUDED.status = 'active'
                  THEN NULL
                ELSE tenant_workspaces.archived_at
              END,
              updated_at = now()
            RETURNING
              tenant_id,
              workspace,
              display_name,
              provider,
              scope_kind,
              scope_native_id,
              environment,
              status,
              created_by,
              updated_by,
              registered_at,
              activated_at,
              archived_at,
              updated_at
            """,
            (
                workspace_entry.tenant_id,
                workspace_entry.workspace,
                workspace_entry.display_name,
                workspace_entry.provider,
                workspace_entry.scope_kind,
                workspace_entry.scope_native_id,
                workspace_entry.environment,
                workspace_entry.status,
                workspace_entry.created_by,
                workspace_entry.updated_by,
                workspace_entry.status,
                workspace_entry.status,
            ),
        )
        return _dict_from_cursor_row(cur, cur.fetchone())


def list_tenant_role_bindings(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
) -> list[dict[str, Any]]:
    """Return tenant-level inherited role bindings anchored to one workspace."""
    return list_inherited_tenant_access_bindings(
        conn,
        tenant_id=tenant_id,
        anchor_workspace=anchor_workspace,
    )


def list_inherited_tenant_access_bindings(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
) -> list[dict[str, Any]]:
    """Return inherited tenant access bindings anchored to one workspace."""
    return fetch_all_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          trb.workspace,
          trb.user_id,
          trb.role_id,
          trb.source_workspace,
          trb.applies_to_future_workspaces,
          trb.granted_by,
          trb.granted_at,
          trb.updated_at
        FROM tenant_role_bindings trb
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces anchor
              WHERE anchor.tenant_id = %s
                AND anchor.workspace = %s
            )
            OR EXISTS (
              SELECT 1
              FROM roles anchor_role
              WHERE anchor_role.tenant_id = %s
                AND anchor_role.workspace = %s
            )
          )
        ORDER BY trb.user_id ASC
        """,
        (
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            tenant_id,
            anchor_workspace,
            tenant_id,
            anchor_workspace,
        ),
    )


def list_inherited_tenant_access_bindings_for_source_workspace(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
    source_workspace: str,
) -> list[dict[str, Any]]:
    """Return inherited tenant access bindings that source from one workspace."""
    return fetch_all_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          trb.workspace,
          trb.user_id,
          trb.role_id,
          trb.source_workspace,
          trb.applies_to_future_workspaces,
          trb.granted_by,
          trb.granted_at,
          trb.updated_at
        FROM tenant_role_bindings trb
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND trb.source_workspace = %s
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces anchor
              WHERE anchor.tenant_id = %s
                AND anchor.workspace = %s
            )
            OR EXISTS (
              SELECT 1
              FROM roles anchor_role
              WHERE anchor_role.tenant_id = %s
                AND anchor_role.workspace = %s
            )
          )
        ORDER BY trb.user_id ASC
        """,
        (
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            source_workspace,
            tenant_id,
            anchor_workspace,
            tenant_id,
            anchor_workspace,
        ),
    )


def list_tenant_admin_audit_events(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
    limit: int = 100,
    offset: int = 0,
    event_category: str | None = None,
    entity_type: str | None = None,
    target_workspace: str | None = None,
    query: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return tenant administration audit history for one tenant."""
    where = [
        "al.tenant_id = %s",
        "(al.event_category = 'tenant_admin' OR al.event_type = 'users.role.assigned_tenant')",
        """(
            EXISTS (
              SELECT 1
              FROM tenant_workspaces anchor
              WHERE anchor.tenant_id = %s
                AND anchor.workspace = %s
            )
            OR EXISTS (
              SELECT 1
              FROM roles anchor_role
              WHERE anchor_role.tenant_id = %s
                AND anchor_role.workspace = %s
            )
          )""",
    ]
    params: list[Any] = [
        tenant_id,
        tenant_id,
        anchor_workspace,
        tenant_id,
        anchor_workspace,
    ]
    if event_category:
        where.append("al.event_category = %s")
        params.append(event_category)
    if entity_type:
        where.append("al.entity_type = %s")
        params.append(entity_type)
    if target_workspace:
        where.append("al.workspace = %s")
        params.append(target_workspace)
    if query:
        where.append(
            """(
                al.workspace ILIKE %s
                OR al.entity_id ILIKE %s
                OR al.event_type ILIKE %s
                OR COALESCE(al.actor_email, '') ILIKE %s
                OR COALESCE(al.actor_id, '') ILIKE %s
              )"""
        )
        query_like = f"%{query}%"
        params.extend([query_like, query_like, query_like, query_like, query_like])

    where_sql = " AND ".join(where)
    rows = fetch_all_dict_conn(
        conn,
        f"""
        SELECT
          al.id,
          al.tenant_id,
          al.workspace,
          al.entity_type,
          al.entity_id,
          al.event_type,
          al.event_category,
          al.previous_value,
          al.new_value,
          al.actor_id,
          al.actor_email,
          al.actor_name,
          al.source,
          al.correlation_id,
          al.created_at
        FROM audit_log al
        WHERE {where_sql}
        ORDER BY al.created_at DESC, al.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    count_row = fetch_one_dict_conn(
        conn,
        f"""
        SELECT COUNT(*)::bigint AS n
        FROM audit_log al
        WHERE {where_sql}
        """,
        tuple(params),
    )
    total = int((count_row or {}).get("n") or 0)
    return rows, total


def get_tenant_role_binding(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return one tenant-level inherited role binding."""
    return get_inherited_tenant_access_binding(
        conn,
        tenant_id=tenant_id,
        anchor_workspace=anchor_workspace,
        user_id=user_id,
    )


def get_inherited_tenant_access_binding(
    conn: Any,
    *,
    tenant_id: str,
    anchor_workspace: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return one inherited tenant access binding."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          trb.workspace,
          trb.user_id,
          trb.role_id,
          trb.source_workspace,
          trb.applies_to_future_workspaces,
          trb.granted_by,
          trb.granted_at,
          trb.updated_at
        FROM tenant_role_bindings trb
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND trb.user_id = %s
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces anchor
              WHERE anchor.tenant_id = %s
                AND anchor.workspace = %s
            )
            OR EXISTS (
              SELECT 1
              FROM roles anchor_role
              WHERE anchor_role.tenant_id = %s
                AND anchor_role.workspace = %s
            )
          )
        """,
        (
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            user_id,
            tenant_id,
            anchor_workspace,
            tenant_id,
            anchor_workspace,
        ),
    )


def upsert_tenant_role_binding(
    conn: Any,
    *,
    binding: TenantRoleBindingUpsert,
) -> dict[str, Any] | None:
    """Create or update one tenant-level inherited role binding idempotently."""
    return upsert_inherited_tenant_access_binding(conn, binding=binding)


def upsert_inherited_tenant_access_binding(
    conn: Any,
    *,
    binding: TenantRoleBindingUpsert,
) -> dict[str, Any] | None:
    """Create or update one inherited tenant access binding idempotently."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenant_role_bindings (
              tenant_id,
              workspace,
              user_id,
              role_id,
              source_workspace,
              applies_to_future_workspaces,
              granted_by,
              granted_at,
              updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (tenant_id, workspace, user_id)
            DO UPDATE SET
              role_id = EXCLUDED.role_id,
              source_workspace = EXCLUDED.source_workspace,
              applies_to_future_workspaces = EXCLUDED.applies_to_future_workspaces,
              granted_by = EXCLUDED.granted_by,
              granted_at = now(),
              updated_at = now()
            RETURNING
              tenant_id,
              workspace,
              user_id,
              role_id,
              source_workspace,
              applies_to_future_workspaces,
              granted_by,
              granted_at,
              updated_at
            """,
            (
                binding.tenant_id,
                TENANT_POLICY_WORKSPACE,
                binding.user_id,
                binding.role_id,
                binding.source_workspace,
                binding.applies_to_future_workspaces,
                binding.granted_by,
            ),
        )
        return _dict_from_cursor_row(cur, cur.fetchone())


def delete_tenant_role_binding(
    conn: Any,
    *,
    tenant_id: str,
    user_id: str,
) -> bool:
    """Delete one tenant-level inherited role binding."""
    return delete_inherited_tenant_access_binding(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def delete_inherited_tenant_access_binding(
    conn: Any,
    *,
    tenant_id: str,
    user_id: str,
) -> bool:
    """Delete one inherited tenant access binding."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM tenant_role_bindings
            WHERE tenant_id = %s
              AND workspace = %s
              AND user_id = %s
            """,
            (tenant_id, TENANT_POLICY_WORKSPACE, user_id),
        )
        return bool(cur.rowcount)


def apply_tenant_role_bindings_to_workspace(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
) -> list[dict[str, Any]]:
    """Apply active future-workspace role bindings to one workspace idempotently."""
    return apply_inherited_tenant_access_to_workspace(
        conn,
        tenant_id=tenant_id,
        workspace=workspace,
    )


def apply_inherited_tenant_access_to_workspace(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
) -> list[dict[str, Any]]:
    """Apply active future-workspace inherited access bindings to one workspace."""
    applied: list[dict[str, Any]] = []
    bindings = list_inherited_tenant_access_bindings(
        conn,
        tenant_id=tenant_id,
        anchor_workspace=workspace,
    )
    for binding in bindings:
        if not bool(binding.get("applies_to_future_workspaces")):
            continue
        user_id = str(binding.get("user_id") or "")
        source_workspace = str(binding.get("source_workspace") or "")
        role_id = str(binding.get("role_id") or "")
        if not user_id or not source_workspace or not role_id:
            continue

        source_user = get_user_by_id(
            conn,
            tenant_id=tenant_id,
            workspace=source_workspace,
            user_id=user_id,
        )
        if source_user is None:
            continue

        create_user(
            conn,
            user=UserUpsert(
                tenant_id=tenant_id,
                workspace=workspace,
                user_id=user_id,
                email=str(source_user.get("email") or ""),
                password_hash=(
                    str(source_user.get("password_hash"))
                    if source_user.get("password_hash") is not None
                    else None
                ),
                full_name=(
                    str(source_user.get("full_name"))
                    if source_user.get("full_name") is not None
                    else None
                ),
                external_id=(
                    str(source_user.get("external_id"))
                    if source_user.get("external_id") is not None
                    else None
                ),
                auth_provider=str(source_user.get("auth_provider") or "local"),
                is_active=bool(source_user.get("is_active")),
                is_superadmin=bool(source_user.get("is_superadmin")),
            ),
        )
        assignment = upsert_user_workspace_role(
            conn,
            assignment=UserWorkspaceRoleUpsert(
                tenant_id=tenant_id,
                workspace=workspace,
                user_id=user_id,
                role_id=role_id,
                granted_by=(
                    str(binding.get("granted_by"))
                    if binding.get("granted_by") is not None
                    else None
                ),
            ),
        )
        applied.append(
            {
                "user_id": user_id,
                "role_id": role_id,
                "source_workspace": source_workspace,
                "workspace": workspace,
                "granted_at": (assignment or {}).get("granted_at"),
            }
        )
    return applied


def bootstrap_rbac_scope(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    template_tenant_id: str = "default",
    template_workspace: str = "default",
) -> None:
    """Seed RBAC roles/permissions into one tenant/workspace scope.

    This helper copies RBAC templates from a source scope into the target
    scope using idempotent inserts.

    Args:
        conn: Open database connection.
        tenant_id: Target tenant identifier.
        workspace: Target workspace identifier.
        template_tenant_id: Source tenant identifier.
        template_workspace: Source workspace identifier.

    Returns:
        None.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO roles (
              tenant_id,
              workspace,
              role_id,
              name,
              description,
              is_system
            )
            SELECT
              %s,
              %s,
              src.role_id,
              src.name,
              src.description,
              src.is_system
            FROM roles src
            WHERE src.tenant_id = %s
              AND src.workspace = %s
            ON CONFLICT (tenant_id, workspace, role_id) DO NOTHING
            """,
            (tenant_id, workspace, template_tenant_id, template_workspace),
        )
        cur.execute(
            """
            INSERT INTO permissions (
              tenant_id,
              workspace,
              permission_id,
              name,
              resource,
              action,
              description
            )
            SELECT
              %s,
              %s,
              src.permission_id,
              src.name,
              src.resource,
              src.action,
              src.description
            FROM permissions src
            WHERE src.tenant_id = %s
              AND src.workspace = %s
            ON CONFLICT (tenant_id, workspace, permission_id) DO NOTHING
            """,
            (tenant_id, workspace, template_tenant_id, template_workspace),
        )
        cur.execute(
            """
            INSERT INTO role_permissions (
              tenant_id,
              workspace,
              role_id,
              permission_id
            )
            SELECT
              %s,
              %s,
              src.role_id,
              src.permission_id
            FROM role_permissions src
            WHERE src.tenant_id = %s
              AND src.workspace = %s
            ON CONFLICT (tenant_id, workspace, role_id, permission_id) DO NOTHING
            """,
            (tenant_id, workspace, template_tenant_id, template_workspace),
        )


def get_role_by_id(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    role_id: str,
) -> dict[str, Any] | None:
    """Return one scoped role row by identifier."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          tenant_id,
          workspace,
          role_id,
          name,
          description,
          is_system,
          created_at,
          updated_at
        FROM roles
        WHERE tenant_id = %s
          AND workspace = %s
          AND role_id = %s
        """,
        (tenant_id, workspace, role_id),
    )


def upsert_user_workspace_role(
    conn: Any,
    *,
    assignment: UserWorkspaceRoleUpsert,
) -> dict[str, Any] | None:
    """Create or update one scoped user-to-role assignment row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_workspace_roles (
              tenant_id,
              workspace,
              user_id,
              role_id,
              granted_by,
              granted_at
            )
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, workspace, user_id)
            DO UPDATE SET
              role_id = EXCLUDED.role_id,
              granted_by = EXCLUDED.granted_by,
              granted_at = now()
            RETURNING
              tenant_id,
              workspace,
              user_id,
              role_id,
              granted_by,
              granted_at
            """,
            (
                assignment.tenant_id,
                assignment.workspace,
                assignment.user_id,
                assignment.role_id,
                assignment.granted_by,
            ),
        )
        return _dict_from_cursor_row(cur, cur.fetchone())


def get_role_permissions(conn: Any, *, tenant_id: str, workspace: str, role_id: str) -> list[str]:
    """Return permission identifiers for one role in scope."""
    rows = fetch_all_dict_conn(
        conn,
        """
        SELECT rp.permission_id
        FROM role_permissions rp
        WHERE rp.tenant_id = %s
          AND rp.workspace = %s
          AND rp.role_id = %s
        ORDER BY rp.permission_id ASC
        """,
        (tenant_id, workspace, role_id),
    )
    return [str(row["permission_id"]) for row in rows if row.get("permission_id")]


def check_permission(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str,
    permission_id: str,
) -> bool:
    """Return True when a user has the requested permission in scope."""
    return permission_id in get_user_permissions(
        conn,
        tenant_id=tenant_id,
        workspace=workspace,
        user_id=user_id,
    )


def touch_user_last_login(conn: Any, *, tenant_id: str, workspace: str, user_id: str) -> None:
    """Update the last login timestamp for one scoped user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET
              last_login_at = now(),
              updated_at = now()
            WHERE tenant_id = %s
              AND workspace = %s
              AND user_id = %s
            """,
            (tenant_id, workspace, user_id),
        )


def upsert_user_session(
    conn: Any,
    *,
    session: SessionUpsert,
) -> dict[str, Any] | None:
    """Create or update a scoped user session by deterministic session_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_sessions (
              tenant_id,
              workspace,
              session_id,
              session_token_hash,
              user_id,
              expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, session_id)
            DO UPDATE SET
              session_token_hash = EXCLUDED.session_token_hash,
              user_id = EXCLUDED.user_id,
              expires_at = EXCLUDED.expires_at
            RETURNING
              tenant_id,
              workspace,
              session_id,
              session_token_hash,
              user_id,
              expires_at,
              created_at
            """,
            (
                session.tenant_id,
                session.workspace,
                session.session_id,
                session.session_token_hash,
                session.user_id,
                session.expires_at,
            ),
        )
        return _dict_from_cursor_row(cur, cur.fetchone())


def delete_session_by_hash(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    session_token_hash: str,
) -> bool:
    """Delete one session by token hash and return whether a row was removed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM user_sessions
            WHERE tenant_id = %s
              AND workspace = %s
              AND session_token_hash = %s
            """,
            (tenant_id, workspace, session_token_hash),
        )
        return bool(cur.rowcount)


def touch_api_key_last_used(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    key_id: str,
) -> None:
    """Set API key last_used_at timestamp for one scoped key identifier."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE api_keys
            SET last_used_at = now()
            WHERE tenant_id = %s
              AND workspace = %s
              AND key_id = %s
            """,
            (tenant_id, workspace, key_id),
        )


def get_user_by_api_key_hash(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    key_hash: str,
) -> dict[str, Any] | None:
    """Resolve active user context from scoped API key hash."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          u.tenant_id,
          u.workspace,
          u.user_id,
          u.email,
          u.full_name,
          u.auth_provider,
          u.is_active,
          u.is_superadmin,
          ak.key_id
        FROM api_keys ak
        JOIN users u
          ON u.tenant_id = ak.tenant_id
         AND u.workspace = ak.workspace
         AND u.user_id = ak.user_id
        WHERE ak.tenant_id = %s
          AND ak.workspace = %s
          AND ak.key_hash = %s
          AND ak.is_active = TRUE
          AND (ak.expires_at IS NULL OR ak.expires_at > now())
          AND u.is_active = TRUE
        LIMIT 1
        """,
        (tenant_id, workspace, key_hash),
    )


def get_inherited_user_by_api_key_hash(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    key_hash: str,
) -> dict[str, Any] | None:
    """Resolve inherited user context from API key hash for a target workspace."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          %s AS workspace,
          u.user_id,
          u.email,
          u.full_name,
          u.auth_provider,
          u.is_active,
          u.is_superadmin,
          ak.key_id,
          trb.source_workspace,
          'inherited' AS assignment_source
        FROM tenant_role_bindings trb
        JOIN api_keys ak
          ON ak.tenant_id = trb.tenant_id
         AND ak.workspace = trb.source_workspace
         AND ak.user_id = trb.user_id
        JOIN users u
          ON u.tenant_id = ak.tenant_id
         AND u.workspace = ak.workspace
         AND u.user_id = ak.user_id
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND trb.applies_to_future_workspaces = TRUE
          AND ak.key_hash = %s
          AND ak.is_active = TRUE
          AND (ak.expires_at IS NULL OR ak.expires_at > now())
          AND u.is_active = TRUE
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces tw
              WHERE tw.tenant_id = %s
                AND tw.workspace = %s
                AND tw.status <> 'archived'
            )
            OR EXISTS (
              SELECT 1
              FROM roles r
              WHERE r.tenant_id = %s
                AND r.workspace = %s
            )
          )
        ORDER BY trb.user_id ASC
        LIMIT 1
        """,
        (
            workspace,
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            key_hash,
            tenant_id,
            workspace,
            tenant_id,
            workspace,
        ),
    )


def get_user_by_session_hash(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    session_token_hash: str,
) -> dict[str, Any] | None:
    """Resolve active user context from scoped session token hash."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          u.tenant_id,
          u.workspace,
          u.user_id,
          u.email,
          u.full_name,
          u.auth_provider,
          u.is_active,
          u.is_superadmin,
          s.session_id,
          s.expires_at
        FROM user_sessions s
        JOIN users u
          ON u.tenant_id = s.tenant_id
         AND u.workspace = s.workspace
         AND u.user_id = s.user_id
        WHERE s.tenant_id = %s
          AND s.workspace = %s
          AND s.session_token_hash = %s
          AND s.expires_at > now()
          AND u.is_active = TRUE
        LIMIT 1
        """,
        (tenant_id, workspace, session_token_hash),
    )


def get_inherited_user_by_session_hash(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    session_token_hash: str,
) -> dict[str, Any] | None:
    """Resolve inherited user context from session token hash for a target workspace."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT
          trb.tenant_id,
          %s AS workspace,
          u.user_id,
          u.email,
          u.full_name,
          u.auth_provider,
          u.is_active,
          u.is_superadmin,
          s.session_id,
          s.expires_at,
          trb.source_workspace,
          'inherited' AS assignment_source
        FROM tenant_role_bindings trb
        JOIN user_sessions s
          ON s.tenant_id = trb.tenant_id
         AND s.workspace = trb.source_workspace
         AND s.user_id = trb.user_id
        JOIN users u
          ON u.tenant_id = s.tenant_id
         AND u.workspace = s.workspace
         AND u.user_id = s.user_id
        WHERE trb.tenant_id = %s
          AND trb.workspace = %s
          AND trb.applies_to_future_workspaces = TRUE
          AND s.session_token_hash = %s
          AND s.expires_at > now()
          AND u.is_active = TRUE
          AND (
            EXISTS (
              SELECT 1
              FROM tenant_workspaces tw
              WHERE tw.tenant_id = %s
                AND tw.workspace = %s
                AND tw.status <> 'archived'
            )
            OR EXISTS (
              SELECT 1
              FROM roles r
              WHERE r.tenant_id = %s
                AND r.workspace = %s
            )
          )
        ORDER BY trb.user_id ASC
        LIMIT 1
        """,
        (
            workspace,
            tenant_id,
            TENANT_POLICY_WORKSPACE,
            session_token_hash,
            tenant_id,
            workspace,
            tenant_id,
            workspace,
        ),
    )


def get_user_permissions(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    user_id: str,
) -> list[str]:
    """Return all effective scoped permissions for a user."""
    effective_role = get_effective_workspace_role(
        conn,
        tenant_id=tenant_id,
        workspace=workspace,
        user_id=user_id,
    )
    if effective_role is None:
        return []

    role_id = str(effective_role.get("role_id") or "")
    source_workspace = str(effective_role.get("source_workspace") or workspace)
    permissions = get_role_permissions(
        conn,
        tenant_id=tenant_id,
        workspace=workspace,
        role_id=role_id,
    )
    if permissions:
        return permissions

    if source_workspace != workspace:
        return get_role_permissions(
            conn,
            tenant_id=tenant_id,
            workspace=source_workspace,
            role_id=role_id,
        )

    return []
