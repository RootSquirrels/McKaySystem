"""Helpers for bounded resource graph context lookups."""

from __future__ import annotations

from typing import Any

from apps.backend.db import fetch_all_dict_conn, fetch_one_dict_conn
from apps.flask_api.utils.payload import _payload_dict


def _as_record(value: Any) -> dict[str, Any]:
    """Return a dict payload when *value* is a dictionary-like object."""
    if isinstance(value, dict):
        return value
    return {}


def _first_non_empty_text(*values: Any) -> str | None:
    """Return the first non-empty normalized text value."""
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _infer_resource_kind(resource_id: str | None, resource_arn: str | None) -> tuple[str, str]:
    """Infer resource type and service from common AWS ids."""
    arn_text = str(resource_arn or "").strip().lower()
    resource_text = str(resource_id or "").strip()
    if arn_text:
        if ":elasticloadbalancing:" in arn_text:
            return "load_balancer", "elbv2"
        if ":lambda:" in arn_text:
            return "function", "lambda"
        if ":rds:" in arn_text:
            return "db_instance", "rds"
        if ":s3:::" in arn_text:
            return "bucket", "s3"
    if resource_text.startswith("i-"):
        return "instance", "ec2"
    if resource_text.startswith("vol-"):
        return "volume", "ec2"
    if resource_text.startswith("vpc-"):
        return "vpc", "vpc"
    if resource_text.startswith("subnet-"):
        return "subnet", "vpc"
    if resource_text.startswith("nat-"):
        return "nat_gateway", "vpc"
    if resource_text.startswith("sg-"):
        return "security_group", "ec2"
    if resource_text.startswith("rtb-"):
        return "route_table", "vpc"
    if resource_text.startswith("tg-"):
        return "target_group", "elbv2"
    return "resource", "unknown"


def _normalize_resource_type(resource_type: str | None) -> str:
    """Normalize common AWS resource type variants to graph-friendly ids."""
    normalized = (_first_non_empty_text(resource_type) or "resource").lower().replace("-", "_")
    aliases = {
        "ebs_volume": "volume",
        "ebs_snapshot": "snapshot",
        "s3_bucket": "bucket",
        "ec2_instance": "instance",
        "security_group": "security_group",
        "nat_gateway": "nat_gateway",
        "db_instance": "db_instance",
    }
    return aliases.get(normalized, normalized)


def _normalize_service(service: str | None, *, resource_type: str | None = None) -> str:
    """Normalize service names to stable graph service ids."""
    normalized = (_first_non_empty_text(service) or "unknown").lower().replace(" ", "")
    normalized_type = _normalize_resource_type(resource_type)
    if normalized_type in {"vpc", "subnet", "nat_gateway", "route_table"}:
        return "vpc"
    if normalized_type in {"instance", "volume", "snapshot", "security_group"}:
        return "ec2"
    if normalized_type == "bucket":
        return "s3"
    if normalized_type in {"load_balancer", "target_group"}:
        return "elbv2"
    if normalized_type == "db_instance":
        return "rds"
    if normalized_type == "function":
        return "lambda"

    aliases = {
        "amazonec2": "ec2",
        "ec2": "ec2",
        "vpc": "vpc",
        "amazons3": "s3",
        "s3": "s3",
        "elasticloadbalancingv2": "elbv2",
        "elbv2": "elbv2",
        "rds": "rds",
        "awslambda": "lambda",
        "lambda": "lambda",
    }
    return aliases.get(normalized, normalized or "unknown")


def graph_resource_key_from_payload(
    payload_value: Any,
    *,
    account_id: str | None = None,
    region: str | None = None,
    service: str | None = None,
) -> str | None:
    """Derive a deterministic graph resource key from a finding-style payload."""
    payload = _payload_dict(payload_value)
    scope = _as_record(payload.get("scope"))
    dimensions = _as_record(payload.get("dimensions"))

    resolved_account_id = _first_non_empty_text(scope.get("account_id"), payload.get("account_id"), account_id)
    if not resolved_account_id:
        return None

    resolved_region = _first_non_empty_text(scope.get("region"), payload.get("region"), region) or ""
    resource_arn = _first_non_empty_text(
        scope.get("resource_arn"),
        payload.get("resource_arn"),
        dimensions.get("resource_arn"),
        dimensions.get("load_balancer_arn"),
    )
    resource_id = _first_non_empty_text(
        scope.get("resource_id"),
        payload.get("resource_id"),
        dimensions.get("resource_id"),
        dimensions.get("instance_id"),
        dimensions.get("bucket"),
        dimensions.get("bucket_name"),
        dimensions.get("db_instance_identifier"),
        dimensions.get("db_cluster_identifier"),
        dimensions.get("nat_gateway_id"),
        dimensions.get("function_name"),
        dimensions.get("load_balancer_name"),
        dimensions.get("volume_id"),
        dimensions.get("snapshot_id"),
        dimensions.get("file_system_id"),
        dimensions.get("vault_name"),
        dimensions.get("plan_name"),
        dimensions.get("cluster_name"),
        dimensions.get("service_name"),
    )
    native_id = resource_arn or resource_id
    if not native_id:
        return None

    inferred_type, inferred_service = _infer_resource_kind(resource_id, resource_arn)
    resolved_resource_type = _first_non_empty_text(
        scope.get("resource_type"),
        payload.get("resource_type"),
        inferred_type,
    ) or "resource"
    normalized_resource_type = _normalize_resource_type(resolved_resource_type)
    resolved_service = _normalize_service(
        scope.get("service"),
        resource_type=normalized_resource_type,
    )
    if resolved_service == "unknown":
        resolved_service = _normalize_service(
            _first_non_empty_text(payload.get("service"), service, inferred_service),
            resource_type=normalized_resource_type,
        )

    return (
        f"aws:{resolved_account_id}:{resolved_region}:{resolved_service}:"
        f"{normalized_resource_type}:{native_id}"
    )


def load_graph_context(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    resource_key: str,
    neighbor_limit: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    """Load one resource plus bounded direct neighbors from current graph tables."""
    resource = fetch_one_dict_conn(
        conn,
        """
        SELECT
          resource_key,
          provider,
          service,
          resource_type,
          account_id,
          region,
          resource_id,
          resource_arn,
          resource_name,
          parent_resource_key,
          state,
          owner_hint,
          is_deleted,
          latest_run_id,
          latest_run_ts
        FROM resource_graph_nodes_current
        WHERE tenant_id = %s AND workspace = %s AND resource_key = %s
        """,
        (tenant_id, workspace, resource_key),
    )
    if not resource:
        return None, [], 0

    total_rows = fetch_all_dict_conn(
        conn,
        """
        SELECT COUNT(*) AS count
        FROM resource_graph_edges_current e
        WHERE e.tenant_id = %s
          AND e.workspace = %s
          AND (e.from_resource_key = %s OR e.to_resource_key = %s)
        """,
        (tenant_id, workspace, resource_key, resource_key),
    )
    total_row = total_rows[0] if total_rows else {"count": 0}

    neighbor_rows = fetch_all_dict_conn(
        conn,
        """
        SELECT
          e.edge_key,
          e.edge_type,
          e.directionality,
          e.confidence,
          e.source_kind,
          e.service AS edge_service,
          e.account_id AS edge_account_id,
          e.region AS edge_region,
          CASE
            WHEN e.from_resource_key = %s THEN 'outgoing'
            ELSE 'incoming'
          END AS direction,
          CASE
            WHEN e.from_resource_key = %s THEN e.to_resource_key
            ELSE e.from_resource_key
          END AS neighbor_resource_key,
          n.service AS neighbor_service,
          n.resource_type AS neighbor_resource_type,
          n.account_id AS neighbor_account_id,
          n.region AS neighbor_region,
          n.resource_id AS neighbor_resource_id,
          n.resource_arn AS neighbor_resource_arn,
          n.resource_name AS neighbor_resource_name,
          n.state AS neighbor_state,
          n.owner_hint AS neighbor_owner_hint,
          n.is_deleted AS neighbor_is_deleted
        FROM resource_graph_edges_current e
        JOIN resource_graph_nodes_current n
          ON n.tenant_id = e.tenant_id
         AND n.workspace = e.workspace
         AND n.resource_key = CASE
           WHEN e.from_resource_key = %s THEN e.to_resource_key
           ELSE e.from_resource_key
         END
        WHERE e.tenant_id = %s
          AND e.workspace = %s
          AND (e.from_resource_key = %s OR e.to_resource_key = %s)
        ORDER BY
          CASE e.source_kind
            WHEN 'api_direct' THEN 0
            WHEN 'derived' THEN 1
            WHEN 'inferred' THEN 2
            ELSE 3
          END,
          CASE e.confidence
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            WHEN 'low' THEN 2
            WHEN 'none' THEN 3
            ELSE 4
          END,
          e.edge_type,
          neighbor_resource_key
        LIMIT %s
        """,
        (
            resource_key,
            resource_key,
            resource_key,
            tenant_id,
            workspace,
            resource_key,
            resource_key,
            neighbor_limit,
        ),
    )

    neighbors = [
        {
            "edge_key": row.get("edge_key"),
            "edge_type": row.get("edge_type"),
            "direction": row.get("direction"),
            "directionality": row.get("directionality"),
            "confidence": row.get("confidence"),
            "source_kind": row.get("source_kind"),
            "service": row.get("edge_service"),
            "account_id": row.get("edge_account_id"),
            "region": row.get("edge_region"),
            "resource": {
                "resource_key": row.get("neighbor_resource_key"),
                "service": row.get("neighbor_service"),
                "resource_type": row.get("neighbor_resource_type"),
                "account_id": row.get("neighbor_account_id"),
                "region": row.get("neighbor_region"),
                "resource_id": row.get("neighbor_resource_id"),
                "resource_arn": row.get("neighbor_resource_arn"),
                "resource_name": row.get("neighbor_resource_name"),
                "state": row.get("neighbor_state"),
                "owner_hint": row.get("neighbor_owner_hint"),
                "is_deleted": row.get("neighbor_is_deleted"),
            },
        }
        for row in neighbor_rows
    ]
    return resource, neighbors, int(total_row.get("count") or 0)
