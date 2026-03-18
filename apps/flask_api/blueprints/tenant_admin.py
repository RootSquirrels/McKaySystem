"""Tenant administration blueprint.

Provides first-class tenant administration endpoints for:
- workspace registry management
- future-workspace inherited role bindings
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from apps.backend import db_rbac
from apps.backend.db import db_conn
from apps.flask_api import auth_middleware
from apps.flask_api.audit import AuditEvent, append_audit_event
from apps.flask_api.auth_middleware import require_permission
from apps.flask_api.utils import (
    _coerce_optional_text,
    _err,
    _ok,
    _parse_bool,
    _parse_int,
    _q,
    _require_scope_from_json,
    _require_scope_from_query,
)
from services.rbac_service import AuthContext

tenant_admin_bp = Blueprint("tenant_admin", __name__)


def _correlation_id() -> str | None:
    """Return request correlation id when present."""
    value = str(
        request.headers.get("X-Correlation-Id")
        or request.headers.get("X-Request-Id")
        or ""
    ).strip()
    return value or None


def _resolved_auth_context() -> AuthContext | None:
    """Return authenticated RBAC context for current request."""
    auth_context = getattr(g, "auth_context", None)
    if isinstance(auth_context, AuthContext):
        return auth_context
    candidate = auth_middleware.authenticate_request()
    return candidate if isinstance(candidate, AuthContext) else None


def _public_workspace(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return public tenant workspace payload."""
    if row is None:
        return None
    return {
        "tenant_id": row.get("tenant_id"),
        "workspace": row.get("workspace"),
        "display_name": row.get("display_name"),
        "provider": row.get("provider"),
        "scope_kind": row.get("scope_kind"),
        "scope_native_id": row.get("scope_native_id"),
        "environment": row.get("environment"),
        "status": row.get("status"),
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
        "registered_at": row.get("registered_at"),
        "activated_at": row.get("activated_at"),
        "archived_at": row.get("archived_at"),
        "updated_at": row.get("updated_at"),
    }


def _public_tenant_binding(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return public tenant role binding payload."""
    if row is None:
        return None
    return {
        "tenant_id": row.get("tenant_id"),
        "workspace": row.get("workspace"),
        "user_id": row.get("user_id"),
        "role_id": row.get("role_id"),
        "source_workspace": row.get("source_workspace"),
        "applies_to_future_workspaces": bool(row.get("applies_to_future_workspaces")),
        "granted_by": row.get("granted_by"),
        "granted_at": row.get("granted_at"),
        "updated_at": row.get("updated_at"),
    }


def _public_audit_event(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return public tenant admin audit event payload."""
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "workspace": row.get("workspace"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "event_type": row.get("event_type"),
        "event_category": row.get("event_category"),
        "previous_value": row.get("previous_value"),
        "new_value": row.get("new_value"),
        "actor_id": row.get("actor_id"),
        "actor_email": row.get("actor_email"),
        "actor_name": row.get("actor_name"),
        "source": row.get("source"),
        "correlation_id": row.get("correlation_id"),
        "created_at": row.get("created_at"),
    }


def _workspace_upsert_from_payload(
    *,
    payload: dict[str, Any],
    tenant_id: str,
    workspace: str,
) -> db_rbac.TenantWorkspaceUpsert:
    """Build validated tenant workspace upsert payload."""
    status = _coerce_optional_text(payload.get("status")) or "active"
    if status not in {"active", "suspended", "archived"}:
        raise ValueError("status must be one of: active, suspended, archived")
    return db_rbac.TenantWorkspaceUpsert(
        tenant_id=tenant_id,
        workspace=workspace,
        display_name=_coerce_optional_text(payload.get("display_name")),
        provider=_coerce_optional_text(payload.get("provider")) or "unknown",
        scope_kind=_coerce_optional_text(payload.get("scope_kind")) or "unknown",
        scope_native_id=_coerce_optional_text(payload.get("scope_native_id")),
        environment=_coerce_optional_text(payload.get("environment")),
        status=status,
        created_by=_coerce_optional_text(payload.get("created_by")),
        updated_by=_coerce_optional_text(payload.get("updated_by")),
    )


def _binding_from_payload(
    *,
    payload: dict[str, Any],
    tenant_id: str,
    anchor_workspace: str,
    user_id: str,
) -> db_rbac.TenantRoleBindingUpsert:
    """Build validated tenant-level role binding payload."""
    uid = _coerce_optional_text(user_id)
    if not uid:
        raise ValueError("user_id is required")
    role_id = _coerce_optional_text(payload.get("role_id"))
    if not role_id:
        raise ValueError("role_id is required")
    source_workspace = _coerce_optional_text(payload.get("source_workspace")) or anchor_workspace
    return db_rbac.TenantRoleBindingUpsert(
        tenant_id=tenant_id,
        user_id=uid,
        role_id=role_id,
        source_workspace=source_workspace,
        granted_by=_coerce_optional_text(payload.get("granted_by")),
        applies_to_future_workspaces=_parse_bool(
            payload.get("applies_to_future_workspaces"),
            field_name="applies_to_future_workspaces",
            default=True,
        ),
    )


@tenant_admin_bp.route("/api/tenant-admin/workspaces", methods=["GET"])
@require_permission("admin:full")
def api_tenant_admin_workspaces_list() -> Any:
    """List registered workspaces for one tenant."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            rows = db_rbac.list_registered_tenant_workspaces(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=workspace,
            )
        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "total": len(rows),
                "items": [_public_workspace(row) for row in rows],
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@tenant_admin_bp.route("/api/tenant-admin/workspaces", methods=["POST"])
@require_permission("admin:full")
def api_tenant_admin_workspaces_create() -> Any:
    """Register or update one tenant workspace entry."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        tenant_id, anchor_workspace = _require_scope_from_json(payload)
        target_workspace = _coerce_optional_text(payload.get("target_workspace"))
        if not target_workspace:
            raise ValueError("target_workspace is required")
        workspace_entry = _workspace_upsert_from_payload(
            payload=payload,
            tenant_id=tenant_id,
            workspace=target_workspace,
        )
        auth_context = _resolved_auth_context()
        if not isinstance(auth_context, AuthContext):
            return _err("unauthorized", "authentication required", status=401)

        with db_conn() as conn:
            previous = db_rbac.get_tenant_workspace(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=anchor_workspace,
                target_workspace=target_workspace,
            )
            row = db_rbac.upsert_tenant_workspace(conn, workspace_entry=workspace_entry)
            append_audit_event(
                conn,
                event=AuditEvent(
                    tenant_id=tenant_id,
                    workspace=anchor_workspace,
                    entity_type="tenant_workspace",
                    entity_id=target_workspace,
                    event_type="tenant_admin.workspace.upserted",
                    event_category="tenant_admin",
                    previous_value=_public_workspace(previous),
                    new_value=_public_workspace(row),
                    actor_id=auth_context.user_id,
                    actor_email=auth_context.email,
                    actor_name=auth_context.full_name,
                    source="/api/tenant-admin/workspaces",
                    correlation_id=_correlation_id(),
                ),
            )
            conn.commit()
        return _ok({"workspace_entry": _public_workspace(row)}, status=201)
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@tenant_admin_bp.route("/api/tenant-admin/workspaces/<target_workspace>", methods=["PUT"])
@require_permission("admin:full")
def api_tenant_admin_workspaces_update(target_workspace: str) -> Any:
    """Update one registered tenant workspace entry."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        tenant_id, anchor_workspace = _require_scope_from_json(payload)
        normalized_target = _coerce_optional_text(target_workspace)
        if not normalized_target:
            raise ValueError("target_workspace is required")
        workspace_entry = _workspace_upsert_from_payload(
            payload=payload,
            tenant_id=tenant_id,
            workspace=normalized_target,
        )
        auth_context = _resolved_auth_context()
        if not isinstance(auth_context, AuthContext):
            return _err("unauthorized", "authentication required", status=401)

        with db_conn() as conn:
            previous = db_rbac.get_tenant_workspace(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=anchor_workspace,
                target_workspace=normalized_target,
            )
            row = db_rbac.upsert_tenant_workspace(conn, workspace_entry=workspace_entry)
            append_audit_event(
                conn,
                event=AuditEvent(
                    tenant_id=tenant_id,
                    workspace=anchor_workspace,
                    entity_type="tenant_workspace",
                    entity_id=normalized_target,
                    event_type="tenant_admin.workspace.updated",
                    event_category="tenant_admin",
                    previous_value=_public_workspace(previous),
                    new_value=_public_workspace(row),
                    actor_id=auth_context.user_id,
                    actor_email=auth_context.email,
                    actor_name=auth_context.full_name,
                    source="/api/tenant-admin/workspaces/<target_workspace>",
                    correlation_id=_correlation_id(),
                ),
            )
            conn.commit()
        return _ok({"workspace_entry": _public_workspace(row)})
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@tenant_admin_bp.route("/api/tenant-admin/role-bindings", methods=["GET"])
@require_permission("admin:full")
def api_tenant_admin_role_bindings_list() -> Any:
    """List tenant-level future-workspace role bindings."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            rows = db_rbac.list_tenant_role_bindings(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=workspace,
            )
        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "total": len(rows),
                "items": [_public_tenant_binding(row) for row in rows],
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@tenant_admin_bp.route("/api/tenant-admin/audit", methods=["GET"])
@require_permission("admin:full")
def api_tenant_admin_audit_list() -> Any:
    """List tenant administration audit events."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        limit = _parse_int(_q("limit"), default=50, min_v=1, max_v=500)
        offset = _parse_int(_q("offset"), default=0, min_v=0, max_v=5_000_000)
        with db_conn() as conn:
            rows, total = db_rbac.list_tenant_admin_audit_events(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=workspace,
                limit=limit,
                offset=offset,
            )
        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "limit": limit,
                "offset": offset,
                "total": total,
                "items": [_public_audit_event(row) for row in rows],
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@tenant_admin_bp.route("/api/tenant-admin/users/<user_id>/role-binding", methods=["PUT"])
@require_permission("admin:full")
def api_tenant_admin_role_binding_upsert(user_id: str) -> Any:
    """Create or update one tenant-level future-workspace role binding."""
    try:
        payload = request.get_json(force=True, silent=False) or {}
        tenant_id, anchor_workspace = _require_scope_from_json(payload)
        binding = _binding_from_payload(
            payload=payload,
            tenant_id=tenant_id,
            anchor_workspace=anchor_workspace,
            user_id=user_id,
        )
        auth_context = _resolved_auth_context()
        if not isinstance(auth_context, AuthContext):
            return _err("unauthorized", "authentication required", status=401)

        with db_conn() as conn:
            source_user = db_rbac.get_user_by_id(
                conn,
                tenant_id=tenant_id,
                workspace=binding.source_workspace,
                user_id=binding.user_id,
            )
            if source_user is None:
                return _err("not_found", "source user not found", status=404)

            role = db_rbac.get_role_by_id(
                conn,
                tenant_id=tenant_id,
                workspace=binding.source_workspace,
                role_id=binding.role_id,
            )
            if role is None:
                return _err("not_found", "role not found in source workspace", status=404)

            previous = db_rbac.get_tenant_role_binding(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=anchor_workspace,
                user_id=binding.user_id,
            )
            row = db_rbac.upsert_tenant_role_binding(conn, binding=binding)
            append_audit_event(
                conn,
                event=AuditEvent(
                    tenant_id=tenant_id,
                    workspace=anchor_workspace,
                    entity_type="tenant_role_binding",
                    entity_id=binding.user_id,
                    event_type="tenant_admin.role_binding.upserted",
                    event_category="tenant_admin",
                    previous_value=_public_tenant_binding(previous),
                    new_value=_public_tenant_binding(row),
                    actor_id=auth_context.user_id,
                    actor_email=auth_context.email,
                    actor_name=auth_context.full_name,
                    source="/api/tenant-admin/users/<user_id>/role-binding",
                    correlation_id=_correlation_id(),
                ),
            )
            conn.commit()
        return _ok({"binding": _public_tenant_binding(row)})
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@tenant_admin_bp.route("/api/tenant-admin/users/<user_id>/role-binding", methods=["DELETE"])
@require_permission("admin:full")
def api_tenant_admin_role_binding_delete(user_id: str) -> Any:
    """Delete one tenant-level future-workspace role binding."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        uid = _coerce_optional_text(user_id)
        if not uid:
            raise ValueError("user_id is required")
        auth_context = _resolved_auth_context()
        if not isinstance(auth_context, AuthContext):
            return _err("unauthorized", "authentication required", status=401)

        with db_conn() as conn:
            previous = db_rbac.get_tenant_role_binding(
                conn,
                tenant_id=tenant_id,
                anchor_workspace=workspace,
                user_id=uid,
            )
            changed = db_rbac.delete_tenant_role_binding(
                conn,
                tenant_id=tenant_id,
                user_id=uid,
            )
            if not changed:
                return _err("not_found", "tenant role binding not found", status=404)
            append_audit_event(
                conn,
                event=AuditEvent(
                    tenant_id=tenant_id,
                    workspace=workspace,
                    entity_type="tenant_role_binding",
                    entity_id=uid,
                    event_type="tenant_admin.role_binding.deleted",
                    event_category="tenant_admin",
                    previous_value=_public_tenant_binding(previous),
                    new_value=None,
                    actor_id=auth_context.user_id,
                    actor_email=auth_context.email,
                    actor_name=auth_context.full_name,
                    source="/api/tenant-admin/users/<user_id>/role-binding",
                    correlation_id=_correlation_id(),
                ),
            )
            conn.commit()
        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "user_id": uid,
                "deleted": True,
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)
