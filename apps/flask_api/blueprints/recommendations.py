"""Recommendations Blueprint.

Provides recommendation endpoints for FinOps optimization opportunities.
"""

from collections import defaultdict
from typing import Any

from flask import Blueprint, request

from apps.backend.db import db_conn, fetch_all_dict_conn, fetch_one_dict_conn
from apps.flask_api.auth_middleware import require_permission
from apps.flask_api.graph_context import graph_resource_key_from_payload
from apps.flask_api.utils import (
    _coerce_non_negative_int,
    _coerce_optional_float,
    _coerce_optional_text,
    _coerce_positive_int,
    _coerce_text_list,
    _err,
    _ok,
    _parse_csv_list,
    _parse_int,
    _q,
    _require_scope_from_json,
    _require_scope_from_query,
)
from apps.flask_api.utils.payload import (
    _as_float,
    _payload_dict,
    _payload_estimated_confidence,
    _payload_pricing_source,
    _payload_pricing_version,
    _run_meta_pricing_source,
    _run_meta_pricing_version,
)

recommendations_bp = Blueprint("recommendations", __name__)


# Recommendation rules keyed by check_id.
_RECOMMENDATION_RULES: dict[str, dict[str, Any]] = {
    "aws.ec2.instances.underutilized": {
        "recommendation_type": "rightsizing.ec2.instance",
        "action": "Downsize EC2 instance to a smaller family/size based on sustained utilization.",
        "priority": "p1",
        "action_type": "rightsize",
        "target_kind": "instance_type",
        "target_value": "smaller_same_family",
        "current_kind": "instance_type",
        "current_value": "current",
        "confidence": 78,
        "pricing_source": "finding_estimate",
        "requires_approval": False,
    },
    "aws.ec2.ri.coverage.gap": {
        "recommendation_type": "commitment.ec2.ri.coverage",
        "action": "Increase EC2 Reserved Instance coverage for steady-state usage.",
        "priority": "p1",
        "action_type": "purchase",
        "target_kind": "coverage_pct",
        "target_value": "90",
        "current_kind": "coverage_pct",
        "current_value": "current",
        "confidence": 66,
        "pricing_source": "finding_estimate",
        "requires_approval": True,
    },
    "aws.ec2.ri.utilization.low": {
        "recommendation_type": "commitment.ec2.ri.utilization",
        "action": "Optimize underutilized EC2 Reserved Instance commitments.",
        "priority": "p1",
        "action_type": "tune",
        "target_kind": "utilization_pct",
        "target_value": ">=80",
        "current_kind": "utilization_pct",
        "current_value": "current",
        "confidence": 60,
        "pricing_source": "finding_estimate",
        "requires_approval": True,
    },
    "aws.ec2.savings.plans.coverage.gap": {
        "recommendation_type": "commitment.ec2.savings_plan.coverage",
        "action": "Increase EC2 Savings Plan commitment for steady-state demand.",
        "priority": "p1",
        "action_type": "purchase",
        "target_kind": "commitment_usd_per_hour",
        "target_value": "match_demand",
        "current_kind": "commitment_usd_per_hour",
        "current_value": "current",
        "confidence": 66,
        "pricing_source": "finding_estimate",
        "requires_approval": True,
    },
    "aws.ec2.savings.plans.utilization.low": {
        "recommendation_type": "commitment.ec2.savings_plan.utilization",
        "action": "Optimize underutilized EC2 Savings Plan commitments.",
        "priority": "p1",
        "action_type": "tune",
        "target_kind": "utilization_pct",
        "target_value": ">=80",
        "current_kind": "utilization_pct",
        "current_value": "current",
        "confidence": 60,
        "pricing_source": "finding_estimate",
        "requires_approval": True,
    },
    "aws.rds.storage.overprovisioned": {
        "recommendation_type": "rightsizing.rds.storage",
        "action": "Reduce allocated RDS storage to match observed baseline plus safety headroom.",
        "priority": "p1",
        "action_type": "rightsize",
        "target_kind": "storage",
        "target_value": "lower_allocated_gb",
        "current_kind": "storage",
        "current_value": "current",
        "confidence": 74,
        "pricing_source": "finding_estimate",
        "requires_approval": False,
    },
    "aws.ec2.nat.gateways.idle": {
        "recommendation_type": "cleanup.nat.gateway",
        "action": "Delete idle NAT Gateway after dependency validation to remove fixed hourly cost.",
        "priority": "p1",
        "action_type": "terminate",
        "target_kind": "resource",
        "target_value": "delete",
        "current_kind": "resource",
        "current_value": "current",
        "confidence": 90,
        "pricing_source": "finding_estimate",
        "requires_approval": True,
    },
    "aws.s3.governance.lifecycle.missing": {
        "recommendation_type": "storage.lifecycle.s3",
        "action": "Add S3 lifecycle transitions/expiration for cold or stale data classes.",
        "priority": "p2",
        "action_type": "enable",
        "target_kind": "feature",
        "target_value": "s3_lifecycle",
        "current_kind": "feature",
        "current_value": "missing",
        "confidence": 65,
        "pricing_source": "finding_estimate",
        "requires_approval": False,
    },
    "aws.lambda.functions.unused": {
        "recommendation_type": "cleanup.lambda.function",
        "action": "Disable triggers or remove unused Lambda functions after owner confirmation.",
        "priority": "p1",
        "action_type": "terminate",
        "target_kind": "resource",
        "target_value": "delete_or_disable",
        "current_kind": "resource",
        "current_value": "current",
        "confidence": 70,
        "pricing_source": "finding_estimate",
        "requires_approval": True,
    },
    "aws.lambda.functions.memory.overprovisioned": {
        "recommendation_type": "rightsizing.lambda.memory",
        "action": "Lower Lambda memory configuration based on p95 memory usage and latency guardrails.",
        "priority": "p1",
        "action_type": "tune",
        "target_kind": "memory_mb",
        "target_value": "lower_memory",
        "current_kind": "memory_mb",
        "current_value": "current",
        "confidence": 72,
        "pricing_source": "finding_estimate",
        "requires_approval": False,
    },
}

_RECOMMENDATION_CHECK_IDS = sorted(_RECOMMENDATION_RULES)
_RECOMMENDATION_DEFAULT_RULE: dict[str, Any] = {
    "recommendation_type": "other",
    "action": "Review finding details and define a remediation plan.",
    "priority": "p2",
    "action_type": "tune",
    "target_kind": "generic",
    "target_value": "review",
    "current_kind": "generic",
    "current_value": "current",
    "confidence": 50,
    "pricing_source": "finding_estimate",
    "requires_approval": False,
}

_GRAPH_PACKAGE_SAMPLE_LIMIT = 3


def _checker_advice(payload: dict[str, Any]) -> str:
    """Return checker-authored guidance text from canonical/adaptive payload fields."""
    advice = str(payload.get("advice") or "").strip()
    if advice:
        return advice
    return str(payload.get("recommendation") or "").strip()


def _row_graph_resource_key(row: dict[str, Any]) -> str | None:
    """Return the primary graph resource key for one recommendation row."""

    payload = _payload_dict(row.get("payload"))
    return graph_resource_key_from_payload(
        payload,
        account_id=str(row.get("account_id") or ""),
        region=str(row.get("region") or ""),
        service=str(row.get("service") or ""),
    )


def _package_kind_for_row(row: dict[str, Any], sample_neighbors: list[dict[str, Any]]) -> str:
    """Return a deterministic package kind for one recommendation row."""

    recommendation_type = str(_RECOMMENDATION_RULES.get(str(row.get("check_id") or ""), _RECOMMENDATION_DEFAULT_RULE)["recommendation_type"])
    neighbor_types = {str(neighbor.get("resource_type") or "") for neighbor in sample_neighbors}
    edge_types = {str(neighbor.get("edge_type") or "") for neighbor in sample_neighbors}

    if recommendation_type == "cleanup.nat.gateway":
        return "nat_dependency_package"
    if "load_balancer" in neighbor_types or "target_group" in neighbor_types or "forwards_to" in edge_types:
        return "ingress_dependency_package"
    if "volume" in neighbor_types or "attached_to" in edge_types:
        return "storage_lineage_package"
    if recommendation_type.startswith("rightsizing.ec2") or "instance" in neighbor_types:
        return "compute_dependency_package"
    if recommendation_type.startswith("storage."):
        return "storage_dependency_package"
    return "resource_context_package"


def _sorted_related_services(sample_neighbors: list[dict[str, Any]]) -> list[str]:
    """Return stable related service ids from neighbor samples."""

    return sorted(
        {
            str(neighbor.get("service") or "").strip()
            for neighbor in sample_neighbors
            if str(neighbor.get("service") or "").strip()
        }
    )


def _owner_hint_candidates(sample_neighbors: list[dict[str, Any]]) -> list[str]:
    """Return stable owner-hint candidates from sampled graph neighbors."""

    return sorted(
        {
            str(neighbor.get("owner_hint") or "").strip()
            for neighbor in sample_neighbors
            if str(neighbor.get("owner_hint") or "").strip()
        }
    )


def _package_reason(package_kind: str, total_neighbors: int) -> str:
    """Return a deterministic explanation for the package."""

    if package_kind == "nat_dependency_package":
        return (
            f"Graph context found {total_neighbors} directly related network resource(s), so NAT cleanup should be "
            "validated against routing dependencies first."
        )
    if package_kind == "ingress_dependency_package":
        return (
            f"Graph context found {total_neighbors} directly related ingress resource(s), so the recommendation "
            "should be reviewed as part of the load balancer and target chain."
        )
    if package_kind == "storage_lineage_package":
        return (
            f"Graph context found {total_neighbors} directly related storage lineage resource(s), so cleanup should "
            "be validated against attached compute and recovery dependencies."
        )
    if package_kind == "compute_dependency_package":
        return (
            f"Graph context found {total_neighbors} directly related compute dependency resource(s), so rightsizing "
            "or cleanup should be reviewed in workload context."
        )
    if package_kind == "storage_dependency_package":
        return (
            f"Graph context found {total_neighbors} directly related storage resource(s), so the recommendation "
            "should be packaged with adjacent storage dependencies."
        )
    return f"Graph context found {total_neighbors} directly related resource(s) for this recommendation."


def _package_title(package_kind: str) -> str:
    """Return a short package title."""

    titles = {
        "nat_dependency_package": "Validate NAT routing dependencies before cleanup",
        "ingress_dependency_package": "Review ingress dependency chain before remediation",
        "storage_lineage_package": "Validate storage lineage before cleanup",
        "compute_dependency_package": "Review compute dependencies before remediation",
        "storage_dependency_package": "Review storage dependencies before remediation",
        "resource_context_package": "Review related resources before remediation",
    }
    return titles.get(package_kind, "Review related resources before remediation")


def _dependency_checklist(package_kind: str, sample_neighbors: list[dict[str, Any]]) -> list[str]:
    """Return a stable bounded checklist for the package kind."""

    neighbor_types = {str(neighbor.get("resource_type") or "") for neighbor in sample_neighbors}
    edge_types = {str(neighbor.get("edge_type") or "") for neighbor in sample_neighbors}

    if package_kind == "nat_dependency_package":
        checklist = [
            "Confirm private subnets have alternate egress or required VPC endpoints before deletion.",
            "Validate route paths that still traverse this NAT gateway.",
        ]
        if "subnet" in neighbor_types or "routes_via" in edge_types:
            checklist.append("Review impacted subnets and their outbound dependency paths.")
        return checklist

    if package_kind == "ingress_dependency_package":
        checklist = [
            "Validate target groups, listeners, and downstream compute before deleting or consolidating ingress.",
            "Confirm health-check and forwarding configuration is intentionally removable.",
        ]
        if "target_group" in neighbor_types or "routes_to" in edge_types:
            checklist.append("Check whether traffic is still expected to route through attached target groups.")
        return checklist

    if package_kind == "storage_lineage_package":
        checklist = [
            "Confirm attached or recently related compute no longer requires this storage asset.",
            "Snapshot or preserve recovery lineage before deletion where required.",
        ]
        if "instance" in neighbor_types or "attached_to" in edge_types:
            checklist.append("Verify instance lineage and mount expectations before cleanup.")
        return checklist

    if package_kind == "compute_dependency_package":
        return [
            "Validate workload dependencies and rollback path before rightsizing or cleanup.",
            "Confirm adjacent storage and network dependencies are still compatible with the target state.",
        ]

    return [
        "Review directly related resources before applying the remediation.",
        "Validate rollback and owner alignment for connected infrastructure.",
    ]


def _build_graph_package(
    *,
    row: dict[str, Any],
    resource_key: str,
    sample_neighbors: list[dict[str, Any]],
    total_neighbors: int,
) -> dict[str, Any]:
    """Build one graph-aware package summary for a recommendation row."""

    package_kind = _package_kind_for_row(row, sample_neighbors)
    owner_hint_candidates = _owner_hint_candidates(sample_neighbors)
    package_owner_hint = owner_hint_candidates[0] if owner_hint_candidates else None
    return {
        "package_key": f"pkg:{resource_key}",
        "package_kind": package_kind,
        "package_title": _package_title(package_kind),
        "package_reason": _package_reason(package_kind, total_neighbors),
        "related_resource_count": total_neighbors,
        "blast_radius": _blast_radius_for_neighbors(total_neighbors),
        "related_services": _sorted_related_services(sample_neighbors),
        "owner_hint": package_owner_hint,
        "owner_hint_candidates": owner_hint_candidates,
        "dependency_checklist": _dependency_checklist(package_kind, sample_neighbors),
        "sample_related_resources": sample_neighbors,
    }


def _actionability_label(score: int) -> str:
    """Return a stable actionability label for a numeric score."""

    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _package_actionability_score(
    *,
    package_monthly_savings: float,
    confidence: int,
    blast_radius: str,
    requires_approval: bool,
    has_owner_hint: bool,
) -> tuple[int, str]:
    """Compute a deterministic package actionability score."""

    score = 35
    if package_monthly_savings >= 200.0:
        score += 20
    elif package_monthly_savings >= 75.0:
        score += 14
    elif package_monthly_savings >= 25.0:
        score += 8

    score += min(20, max(0, int(round(confidence / 5.0))))

    if has_owner_hint:
        score += 12
    if not requires_approval:
        score += 8

    blast_radius_penalty = {"low": 0, "medium": 8, "high": 16}
    score -= blast_radius_penalty.get(blast_radius, 10)

    score = max(0, min(100, score))
    return score, _actionability_label(score)


def _cluster_package_rows(
    *,
    rows_by_resource_key: dict[str, list[dict[str, Any]]],
    package_kind_by_resource_key: dict[str, str],
    related_resource_keys_by_root: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Build deterministic package clusters from overlapping graph neighborhoods."""

    resource_keys = sorted(rows_by_resource_key)
    parents = {resource_key: resource_key for resource_key in resource_keys}

    def _find(resource_key: str) -> str:
        while parents[resource_key] != resource_key:
            parents[resource_key] = parents[parents[resource_key]]
            resource_key = parents[resource_key]
        return resource_key

    def _union(left: str, right: str) -> None:
        left_root = _find(left)
        right_root = _find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parents[right_root] = left_root
        else:
            parents[left_root] = right_root

    for idx, left in enumerate(resource_keys):
        left_kind = package_kind_by_resource_key.get(left, "")
        left_related = related_resource_keys_by_root.get(left, {left})
        for right in resource_keys[idx + 1 :]:
            if package_kind_by_resource_key.get(right, "") != left_kind:
                continue
            right_related = related_resource_keys_by_root.get(right, {right})
            if left_related.intersection(right_related):
                _union(left, right)

    clusters: dict[str, set[str]] = defaultdict(set)
    for resource_key in resource_keys:
        clusters[_find(resource_key)].add(resource_key)
    return clusters


def _row_confidence_for_sort(row: dict[str, Any]) -> int:
    """Return one row confidence value for deterministic owner selection."""

    payload = _payload_dict(row.get("payload"))
    check_id = str(row.get("check_id") or "")
    rule = _RECOMMENDATION_RULES.get(check_id, _RECOMMENDATION_DEFAULT_RULE)
    confidence = _payload_estimated_confidence(payload)
    if confidence is None:
        confidence = int(rule.get("confidence") or 0)
    return int(confidence)


def _choose_cluster_owner(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the deterministic savings owner for one package cluster."""

    return sorted(
        rows,
        key=lambda row: (
            -_as_float(row.get("estimated_monthly_savings"), default=0.0),
            -_row_confidence_for_sort(row),
            str(row.get("fingerprint") or ""),
        ),
    )[0]


def _apply_package_savings_ownership(
    *,
    rows_by_resource_key: dict[str, list[dict[str, Any]]],
    graph_packages: dict[str, dict[str, Any]],
    package_kind_by_resource_key: dict[str, str],
    related_resource_keys_by_root: dict[str, set[str]],
) -> None:
    """Annotate graph packages with package-level savings ownership metadata."""

    clusters = _cluster_package_rows(
        rows_by_resource_key=rows_by_resource_key,
        package_kind_by_resource_key=package_kind_by_resource_key,
        related_resource_keys_by_root=related_resource_keys_by_root,
    )

    for cluster_root in sorted(clusters):
        cluster_resource_keys = sorted(clusters[cluster_root])
        cluster_rows: list[dict[str, Any]] = []
        for resource_key in cluster_resource_keys:
            cluster_rows.extend(rows_by_resource_key.get(resource_key, []))
        if not cluster_rows:
            continue

        owner_row = _choose_cluster_owner(cluster_rows)
        owner_fingerprint = str(owner_row.get("fingerprint") or "")
        package_monthly_savings = round(
            sum(_as_float(row.get("estimated_monthly_savings"), default=0.0) for row in cluster_rows),
            2,
        )
        package_annual_savings = round(package_monthly_savings * 12.0, 2)
        suppressed_fingerprints = sorted(
            {
                str(row.get("fingerprint") or "")
                for row in cluster_rows
                if str(row.get("fingerprint") or "") and str(row.get("fingerprint") or "") != owner_fingerprint
            }
        )
        package_cluster_key = f"pkgcluster:{cluster_root}"

        for resource_key in cluster_resource_keys:
            package = graph_packages.get(resource_key)
            if package is None:
                continue
            package["package_cluster_key"] = package_cluster_key
            package["package_estimated_monthly_savings"] = package_monthly_savings
            package["package_estimated_annual_savings"] = package_annual_savings
            package["savings_owner_fingerprint"] = owner_fingerprint
            package["suppressed_fingerprints"] = suppressed_fingerprints
            package["package_owner_hint"] = package.get("owner_hint")
            actionability_score, actionability_label = _package_actionability_score(
                package_monthly_savings=package_monthly_savings,
                confidence=_row_confidence_for_sort(owner_row),
                blast_radius=str(package.get("blast_radius") or "medium"),
                requires_approval=bool(
                    _RECOMMENDATION_RULES.get(
                        str(owner_row.get("check_id") or ""),
                        _RECOMMENDATION_DEFAULT_RULE,
                    ).get("requires_approval")
                ),
                has_owner_hint=bool(str(package.get("owner_hint") or "").strip()),
            )
            package["actionability_score"] = actionability_score
            package["actionability_label"] = actionability_label

    for resource_key, rows in rows_by_resource_key.items():
        package = graph_packages.get(resource_key)
        if package is None:
            continue
        owner_fingerprint = str(package.get("savings_owner_fingerprint") or "")
        package_monthly_savings = _as_float(package.get("package_estimated_monthly_savings"), default=0.0)
        package_annual_savings = _as_float(package.get("package_estimated_annual_savings"), default=0.0)
        for row in rows:
            fingerprint = str(row.get("fingerprint") or "")
            is_owner = fingerprint == owner_fingerprint
            row["_graph_package_owner"] = is_owner
            row["_effective_estimated_monthly_savings"] = package_monthly_savings if is_owner else 0.0
            row["_effective_estimated_annual_savings"] = package_annual_savings if is_owner else 0.0
            row["_suppressed_by_fingerprint"] = None if is_owner else owner_fingerprint


def _blast_radius_for_neighbors(total_neighbors: int) -> str:
    """Return a bounded blast-radius hint from graph neighborhood size."""

    if total_neighbors >= 6:
        return "high"
    if total_neighbors >= 3:
        return "medium"
    return "low"


def _load_graph_packages_for_rows(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Load bounded graph package summaries for recommendation rows."""

    rows_by_resource_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        resource_key = _row_graph_resource_key(row)
        if resource_key:
            rows_by_resource_key[resource_key].append(row)

    if not rows_by_resource_key:
        return {}

    resource_keys = sorted(rows_by_resource_key)
    graph_rows = fetch_all_dict_conn(
        conn,
        """
        WITH relevant AS (
          SELECT
            CASE
              WHEN e.from_resource_key = ANY(%s) THEN e.from_resource_key
              ELSE e.to_resource_key
            END AS root_resource_key,
            e.edge_type,
            e.source_kind,
            e.confidence,
            CASE
              WHEN e.from_resource_key = ANY(%s) THEN e.to_resource_key
              ELSE e.from_resource_key
            END AS neighbor_resource_key,
            n.service AS neighbor_service,
            n.resource_type AS neighbor_resource_type,
            n.resource_name AS neighbor_resource_name,
            n.owner_hint AS neighbor_owner_hint
          FROM resource_graph_edges_current e
          JOIN resource_graph_nodes_current n
            ON n.tenant_id = e.tenant_id
           AND n.workspace = e.workspace
           AND n.resource_key = CASE
             WHEN e.from_resource_key = ANY(%s) THEN e.to_resource_key
             ELSE e.from_resource_key
           END
          WHERE e.tenant_id = %s
            AND e.workspace = %s
            AND (e.from_resource_key = ANY(%s) OR e.to_resource_key = ANY(%s))
        ),
        ranked AS (
          SELECT
            root_resource_key,
            edge_type,
            source_kind,
            confidence,
            neighbor_resource_key,
            neighbor_service,
            neighbor_resource_type,
            neighbor_resource_name,
            neighbor_owner_hint,
            ROW_NUMBER() OVER (
              PARTITION BY root_resource_key
              ORDER BY
                CASE source_kind
                  WHEN 'api_direct' THEN 0
                  WHEN 'derived' THEN 1
                  WHEN 'inferred' THEN 2
                  ELSE 3
                END,
                CASE confidence
                  WHEN 'high' THEN 0
                  WHEN 'medium' THEN 1
                  WHEN 'low' THEN 2
                  ELSE 3
                END,
                edge_type,
                neighbor_resource_key
            ) AS rn,
            COUNT(*) OVER (PARTITION BY root_resource_key) AS total_neighbors
          FROM relevant
        )
        SELECT
          root_resource_key,
          edge_type,
          source_kind,
          confidence,
          neighbor_resource_key,
          neighbor_service,
          neighbor_resource_type,
          neighbor_resource_name,
          neighbor_owner_hint,
          total_neighbors
        FROM ranked
        WHERE rn <= %s
        ORDER BY root_resource_key, rn
        """,
        (
            resource_keys,
            resource_keys,
            resource_keys,
            tenant_id,
            workspace,
            resource_keys,
            resource_keys,
            _GRAPH_PACKAGE_SAMPLE_LIMIT,
        ),
    )

    grouped_neighbors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_neighbors_by_root: dict[str, int] = {}
    related_resource_keys_by_root: dict[str, set[str]] = defaultdict(set)
    for graph_row in graph_rows:
        root_resource_key = str(graph_row.get("root_resource_key") or "").strip()
        if not root_resource_key:
            continue
        total_neighbors_by_root[root_resource_key] = int(graph_row.get("total_neighbors") or 0)
        related_resource_keys_by_root[root_resource_key].add(root_resource_key)
        neighbor_resource_key = str(graph_row.get("neighbor_resource_key") or "").strip()
        if neighbor_resource_key:
            related_resource_keys_by_root[root_resource_key].add(neighbor_resource_key)
        grouped_neighbors[root_resource_key].append(
            {
                "resource_key": graph_row.get("neighbor_resource_key"),
                "service": graph_row.get("neighbor_service"),
                "resource_type": graph_row.get("neighbor_resource_type"),
                "resource_name": graph_row.get("neighbor_resource_name"),
                "owner_hint": graph_row.get("neighbor_owner_hint"),
                "edge_type": graph_row.get("edge_type"),
                "source_kind": graph_row.get("source_kind"),
                "confidence": graph_row.get("confidence"),
            }
        )

    packages: dict[str, dict[str, Any]] = {}
    package_kind_by_resource_key: dict[str, str] = {}
    for resource_key, related_rows in rows_by_resource_key.items():
        sample_neighbors = grouped_neighbors.get(resource_key, [])
        if not sample_neighbors:
            continue
        total_neighbors = total_neighbors_by_root.get(resource_key, len(sample_neighbors))
        package_kind_by_resource_key[resource_key] = _package_kind_for_row(related_rows[0], sample_neighbors)
        packages[resource_key] = _build_graph_package(
            row=related_rows[0],
            resource_key=resource_key,
            sample_neighbors=sample_neighbors,
            total_neighbors=total_neighbors,
        )
    _apply_package_savings_ownership(
        rows_by_resource_key=rows_by_resource_key,
        graph_packages=packages,
        package_kind_by_resource_key=package_kind_by_resource_key,
        related_resource_keys_by_root=related_resource_keys_by_root,
    )
    return packages


def _build_recommendations_where_from_values(
    tenant_id: str,
    workspace: str,
    *,
    effective_states: list[str] | None,
    severities: list[str] | None,
    services: list[str] | None,
    check_ids: list[str] | None,
    categories: list[str] | None,
    regions: list[str] | None,
    account_ids: list[str] | None,
    query_str: str | None,
    min_savings: float | None,
    fingerprints: list[str] | None = None,
) -> tuple[list[str], list[Any]]:
    """Build scoped SQL filters for recommendations endpoints."""
    where = ["fc.tenant_id = %s", "fc.workspace = %s", "fc.check_id = ANY(%s)"]
    params: list[Any] = [tenant_id, workspace, _RECOMMENDATION_CHECK_IDS]
    where.append(
        """
        NOT (
          LOWER(COALESCE(fc.title, '')) LIKE 'cannot verify%%'
          OR LOWER(COALESCE(fc.title, '')) LIKE '%%access denied%%'
          OR LOWER(COALESCE(fc.payload->'issue_key'->>'reason', '')) = 'access_denied'
          OR LOWER(COALESCE(fc.payload->>'message', '')) LIKE '%%access denied%%'
          OR LOWER(COALESCE(fc.payload->>'advice', '')) LIKE '%%access denied%%'
          OR LOWER(COALESCE(fc.payload->>'recommendation', '')) LIKE '%%access denied%%'
        )
        """
    )

    def _add_any(field: str, values: list[str] | None) -> None:
        if not values:
            return
        where.append(f"fc.{field} = ANY(%s)")
        params.append(values)

    _add_any("effective_state", effective_states)
    _add_any("severity", severities)
    _add_any("service", services)
    _add_any("check_id", check_ids)
    _add_any("category", categories)
    _add_any("region", regions)
    _add_any("account_id", account_ids)
    _add_any("fingerprint", fingerprints)

    if query_str:
        where.append("fc.title ILIKE %s")
        params.append(f"%{query_str}%")

    if min_savings is not None:
        where.append("COALESCE(fc.estimated_monthly_savings, 0) >= %s")
        params.append(min_savings)

    return where, params


def _build_recommendations_where(tenant_id: str, workspace: str) -> tuple[list[str], list[Any]]:
    """Build scoped SQL filters for recommendations endpoints from query params."""
    min_savings_raw = _q("min_savings")
    min_savings: float | None = None
    if min_savings_raw is not None:
        try:
            min_savings = float(min_savings_raw)
        except ValueError as exc:
            raise ValueError(f"Invalid min_savings: {min_savings_raw}") from exc
    return _build_recommendations_where_from_values(
        tenant_id,
        workspace,
        effective_states=_parse_csv_list(_q("state", "open")),
        severities=_parse_csv_list(_q("severity")),
        services=_parse_csv_list(_q("service")),
        check_ids=_parse_csv_list(_q("check_id")),
        categories=_parse_csv_list(_q("category")),
        regions=_parse_csv_list(_q("region")),
        account_ids=_parse_csv_list(_q("account_id")),
        query_str=_q("q"),
        min_savings=min_savings,
    )


def _recommendation_type_case_sql() -> str:
    """Return SQL CASE expression that maps check_id to recommendation_type."""
    clauses: list[str] = []
    for check_id in _RECOMMENDATION_CHECK_IDS:
        rec_type = _RECOMMENDATION_RULES[check_id]["recommendation_type"]
        clauses.append(f"WHEN check_id = '{check_id}' THEN '{rec_type}'")
    return "CASE " + " ".join(clauses) + " ELSE 'other' END"


def _build_estimate_risk_warnings(
    *,
    items: list[dict[str, Any]],
    requested_fingerprints: list[str] | None,
) -> list[dict[str, Any]]:
    """Build deterministic risk warnings for estimate responses."""
    warnings: list[dict[str, Any]] = []
    requires_approval_count = sum(1 for item in items if bool(item.get("requires_approval")))
    if requires_approval_count:
        warnings.append(
            {
                "code": "approval_required",
                "severity": "medium",
                "count": requires_approval_count,
                "message": "Some recommendations require manual approval before execution.",
            }
        )

    low_confidence_count = sum(1 for item in items if int(item.get("confidence") or 0) < 60)
    if low_confidence_count:
        warnings.append(
            {
                "code": "low_confidence",
                "severity": "low",
                "count": low_confidence_count,
                "message": "Some estimates have low confidence and should be manually validated.",
            }
        )

    if requested_fingerprints is not None:
        found = {str(item.get("fingerprint") or "") for item in items}
        missing = [fp for fp in requested_fingerprints if fp not in found]
        if missing:
            warnings.append(
                {
                    "code": "missing_or_ineligible",
                    "severity": "low",
                    "count": len(missing),
                    "message": "Some requested fingerprints are missing or not recommendation-eligible.",
                    "fingerprints": missing,
                }
            )
    return warnings


def _confidence_label(score: int) -> str:
    """Return a stable label for a confidence-like score."""

    if score >= 80:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _bounded_confidence(score: int) -> int:
    """Clamp a confidence-like score to the 0-100 range."""

    return max(0, min(100, int(score)))


def _build_confidence_component(score: int, factors: list[str]) -> dict[str, Any]:
    """Build one normalized confidence component payload."""

    bounded = _bounded_confidence(score)
    return {
        "score": bounded,
        "label": _confidence_label(bounded),
        "factors": factors,
    }


def _build_confidence_model(
    *,
    check_id: str,
    rule: dict[str, Any],
    payload: dict[str, Any],
    graph_package: dict[str, Any] | None,
    monthly_savings: float,
    pricing_source: str,
    pricing_version: str | None,
    confidence: int,
) -> dict[str, Any]:
    """Build confidence model v1 with issue, savings, and action-safety scores."""

    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}

    issue_score = confidence
    issue_factors = ["base_checker_confidence"]
    optimization_focus = str(dimensions.get("optimization_focus") or "").strip().lower()
    if optimization_focus:
        issue_factors.append("optimization_focus_present")
        if optimization_focus.endswith("_review"):
            issue_score -= 6
            issue_factors.append("review_semantics_reduce_issue_certainty")
        else:
            issue_score += 4
            issue_factors.append("explicit_target_state_increases_issue_certainty")
    if payload.get("estimated") and isinstance(payload.get("estimated"), dict):
        issue_factors.append("checker_estimated_confidence_provided")

    savings_score = confidence
    savings_factors = ["base_checker_confidence"]
    if pricing_source == "snapshot":
        savings_score += 6
        savings_factors.append("snapshot_pricing_source")
    elif pricing_source == "finding_estimate":
        savings_score -= 8
        savings_factors.append("directional_finding_estimate")
    else:
        savings_factors.append("non_snapshot_pricing_source")
    if pricing_version:
        savings_score += 4
        savings_factors.append("pricing_version_present")
    else:
        savings_score -= 4
        savings_factors.append("pricing_version_missing")
    if monthly_savings <= 0.0:
        savings_score -= 15
        savings_factors.append("no_positive_estimated_savings")
    elif monthly_savings >= 100.0:
        savings_score += 2
        savings_factors.append("material_estimated_savings")
    recommendation_type = str(rule.get("recommendation_type") or "")
    if recommendation_type.startswith("storage.lifecycle") or recommendation_type.startswith("commitment."):
        savings_score -= 5
        savings_factors.append("directional_optimization_surface")

    action_safety_score = confidence
    action_safety_factors = ["base_checker_confidence"]
    if bool(rule.get("requires_approval")):
        action_safety_score -= 12
        action_safety_factors.append("manual_approval_required")
    action_type = str(rule.get("action_type") or "").strip().lower()
    if action_type == "terminate":
        action_safety_score -= 10
        action_safety_factors.append("destructive_action_type")
    elif action_type in {"rightsize", "tune"}:
        action_safety_score += 3
        action_safety_factors.append("reversible_optimization_action")
    if graph_package:
        blast_radius = str(graph_package.get("blast_radius") or "").strip().lower()
        if blast_radius == "high":
            action_safety_score -= 15
            action_safety_factors.append("high_blast_radius")
        elif blast_radius == "medium":
            action_safety_score -= 8
            action_safety_factors.append("medium_blast_radius")
        elif blast_radius == "low":
            action_safety_score += 4
            action_safety_factors.append("low_blast_radius")
        if str(graph_package.get("package_owner_hint") or "").strip():
            action_safety_score += 4
            action_safety_factors.append("owner_hint_present")

    return {
        "version": "v1",
        "overall_score": confidence,
        "overall_label": _confidence_label(confidence),
        "issue": _build_confidence_component(issue_score, issue_factors),
        "savings": _build_confidence_component(savings_score, savings_factors),
        "action_safety": _build_confidence_component(action_safety_score, action_safety_factors),
    }


def _build_recommendation_item(
    row: dict[str, Any],
    *,
    graph_packages: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert a finding_current row to a recommendation item payload."""
    check_id = str(row.get("check_id") or "")
    rule = _RECOMMENDATION_RULES.get(check_id, _RECOMMENDATION_DEFAULT_RULE)
    payload = _payload_dict(row.get("payload"))
    run_meta = _payload_dict(row.get("run_meta"))
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}

    monthly_savings = _as_float(row.get("estimated_monthly_savings"), default=0.0)
    annual_savings = round(monthly_savings * 12.0, 2)
    confidence = _payload_estimated_confidence(payload)
    if confidence is None:
        confidence = int(rule.get("confidence") or 0)
    pricing_source = _payload_pricing_source(payload)
    if not pricing_source:
        pricing_source = _run_meta_pricing_source(run_meta)
    if not pricing_source:
        pricing_source = str(rule["pricing_source"])

    pricing_version = _payload_pricing_version(payload)
    if not pricing_version:
        pricing_version_raw = dimensions.get("pricing_version")
        pricing_version = str(pricing_version_raw).strip() if pricing_version_raw is not None else None
    if not pricing_version:
        pricing_version = _run_meta_pricing_version(run_meta)

    action = str(rule["action"])
    target_kind = str(rule["target_kind"])
    target_value = str(rule["target_value"])
    current_kind = str(rule["current_kind"])
    current_value = str(rule["current_value"])

    if check_id == "aws.ec2.instances.underutilized":
        current_instance_type = str(dimensions.get("instance_type") or "").strip()
        target_instance_type = str(dimensions.get("recommended_instance_type") or "").strip()
        if current_instance_type:
            current_value = current_instance_type
        if target_instance_type:
            target_value = target_instance_type
        if current_instance_type and target_instance_type:
            action = (
                f"Downsize EC2 instance from {current_instance_type} to {target_instance_type} "
                "based on sustained utilization."
            )

    if check_id == "aws.rds.storage.overprovisioned":
        allocated_gb = str(dimensions.get("allocated_gb") or "").strip()
        estimated_used_gb = str(dimensions.get("estimated_used_gb") or "").strip()
        if allocated_gb:
            current_value = f"{allocated_gb}gb"
        if estimated_used_gb:
            target_value = f"{estimated_used_gb}gb"
        if allocated_gb and estimated_used_gb:
            action = (
                f"Reduce allocated RDS storage from {allocated_gb} GB toward observed baseline "
                f"({estimated_used_gb} GB) after validating growth headroom."
            )

    if check_id == "aws.ec2.ri.coverage.gap":
        instance_type = str(dimensions.get("instance_type") or "").strip()
        uncovered = str(dimensions.get("uncovered_count") or "").strip()
        coverage_pct = str(dimensions.get("coverage_pct") or "").strip()
        target_coverage_pct = str(dimensions.get("target_coverage_pct") or "").strip()
        if coverage_pct:
            current_value = coverage_pct
        if target_coverage_pct:
            target_value = target_coverage_pct
        if instance_type and uncovered:
            action = (
                f"Increase RI coverage for {instance_type} by about {uncovered} instance(s) "
                "after validating baseline demand."
            )

    if check_id == "aws.ec2.ri.utilization.low":
        instance_type = str(dimensions.get("instance_type") or "").strip()
        unused = str(dimensions.get("unused_count") or "").strip()
        utilization_pct = str(dimensions.get("utilization_pct") or "").strip()
        target_utilization_pct = str(dimensions.get("target_utilization_pct") or "").strip()
        if utilization_pct:
            current_value = utilization_pct
        if target_utilization_pct:
            target_value = target_utilization_pct
        if instance_type and unused:
            action = (
                f"Reduce unused RI commitments for {instance_type} (~{unused} unit(s)) via "
                "modification/exchange/reallocation."
            )

    if check_id == "aws.ec2.savings.plans.coverage.gap":
        demand_hourly = str(dimensions.get("estimated_demand_usd_per_hour") or "").strip()
        committed_hourly = str(dimensions.get("committed_usd_per_hour") or "").strip()
        uncovered_hourly = str(dimensions.get("uncovered_usd_per_hour") or "").strip()
        if committed_hourly:
            current_value = committed_hourly
        if demand_hourly:
            target_value = demand_hourly
        if uncovered_hourly:
            action = (
                f"Increase Savings Plan commitment by about ${uncovered_hourly}/hr "
                "after validating steady-state demand."
            )

    if check_id == "aws.ec2.savings.plans.utilization.low":
        utilization_pct = str(dimensions.get("utilization_pct") or "").strip()
        target_utilization_pct = str(dimensions.get("target_utilization_pct") or "").strip()
        unused_hourly = str(dimensions.get("unused_usd_per_hour") or "").strip()
        if utilization_pct:
            current_value = utilization_pct
        if target_utilization_pct:
            target_value = target_utilization_pct
        if unused_hourly:
            action = (
                f"Reduce underused Savings Plan commitment (about ${unused_hourly}/hr appears unused) "
                "through workload alignment and commitment planning."
            )

    resource_key = _row_graph_resource_key(row)
    graph_package = None
    if resource_key and graph_packages:
        graph_package = graph_packages.get(resource_key)
    effective_monthly_savings = _as_float(
        row.get("_effective_estimated_monthly_savings"),
        default=monthly_savings,
    )
    effective_annual_savings = _as_float(
        row.get("_effective_estimated_annual_savings"),
        default=annual_savings,
    )
    if graph_package:
        actionability_score = int((graph_package or {}).get("actionability_score") or 0)
        actionability_label = str((graph_package or {}).get("actionability_label") or "low")
    else:
        actionability_score, actionability_label = _package_actionability_score(
            package_monthly_savings=effective_monthly_savings,
            confidence=confidence,
            blast_radius="medium",
            requires_approval=bool(rule.get("requires_approval")),
            has_owner_hint=False,
        )
    owner_hint = str((graph_package or {}).get("package_owner_hint") or "").strip() or None
    confidence_model = _build_confidence_model(
        check_id=check_id,
        rule=rule,
        payload=payload,
        graph_package=graph_package if isinstance(graph_package, dict) else None,
        monthly_savings=monthly_savings,
        pricing_source=pricing_source,
        pricing_version=pricing_version,
        confidence=confidence,
    )

    return {
        "fingerprint": row.get("fingerprint"),
        "check_id": check_id,
        "service": row.get("service"),
        "severity": row.get("severity"),
        "category": row.get("category"),
        "title": row.get("title"),
        "recommendation_type": rule["recommendation_type"],
        "action": action,
        "priority": rule["priority"],
        "action_type": rule["action_type"],
        "target": {
            "kind": target_kind,
            "value": target_value,
        },
        # Free text from checker for context, separate from normalized action plan.
        "checker_advice": _checker_advice(payload),
        "current": {
            "kind": current_kind,
            "value": current_value,
        },
        "estimated_monthly_savings": monthly_savings,
        "estimated_annual_savings": annual_savings,
        "effective_estimated_monthly_savings": effective_monthly_savings,
        "effective_estimated_annual_savings": effective_annual_savings,
        "actionability_score": actionability_score,
        "actionability_label": actionability_label,
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "confidence_model": confidence_model,
        "pricing_source": pricing_source,
        "pricing_version": pricing_version,
        "requires_approval": bool(rule.get("requires_approval")),
        "region": row.get("region"),
        "account_id": row.get("account_id"),
        "detected_at": row.get("detected_at"),
        "effective_state": row.get("effective_state"),
        "is_primary_package_savings_owner": bool(row.get("_graph_package_owner", True)),
        "suppressed_by_fingerprint": row.get("_suppressed_by_fingerprint"),
        "owner_hint": owner_hint,
        "resource_key": resource_key,
        "graph_package": graph_package,
        "payload": payload,
    }


def _recommendation_package_group_key(item: dict[str, Any]) -> str:
    """Return the stable grouping key for one recommendation item package."""

    graph_package = item.get("graph_package")
    if isinstance(graph_package, dict):
        cluster_key = str(graph_package.get("package_cluster_key") or "").strip()
        if cluster_key:
            return cluster_key
        package_key = str(graph_package.get("package_key") or "").strip()
        if package_key:
            return package_key
    return f"single:{str(item.get('fingerprint') or '')}"


def _primary_package_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the primary recommendation item for one package."""

    for item in items:
        if bool(item.get("is_primary_package_savings_owner", True)):
            return item
    return items[0]


def _build_recommendation_package(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one package-native recommendation object from member items."""

    primary_item = _primary_package_item(items)
    graph_package = primary_item.get("graph_package")
    package_monthly_savings = round(
        sum(_as_float(item.get("effective_estimated_monthly_savings"), default=0.0) for item in items),
        2,
    )
    package_annual_savings = round(
        sum(_as_float(item.get("effective_estimated_annual_savings"), default=0.0) for item in items),
        2,
    )
    services = sorted({str(item.get("service") or "").strip() for item in items if str(item.get("service") or "").strip()})
    regions = sorted({str(item.get("region") or "").strip() for item in items if str(item.get("region") or "").strip()})
    check_ids = sorted({str(item.get("check_id") or "").strip() for item in items if str(item.get("check_id") or "").strip()})
    owner_hint = str(
        ((graph_package or {}).get("package_owner_hint") if isinstance(graph_package, dict) else "")
        or primary_item.get("owner_hint")
        or ""
    ).strip() or None

    return {
        "package_key": _recommendation_package_group_key(primary_item),
        "package_kind": (
            str((graph_package or {}).get("package_kind") or "").strip()
            if isinstance(graph_package, dict)
            else ""
        )
        or "single_recommendation_package",
        "package_title": (
            str((graph_package or {}).get("package_title") or "").strip()
            if isinstance(graph_package, dict)
            else ""
        )
        or str(primary_item.get("title") or ""),
        "package_reason": (
            str((graph_package or {}).get("package_reason") or "").strip()
            if isinstance(graph_package, dict)
            else ""
        )
        or "Single recommendation package without related graph context.",
        "package_estimated_monthly_savings": package_monthly_savings,
        "package_estimated_annual_savings": package_annual_savings,
        "actionability_score": int(primary_item.get("actionability_score") or 0),
        "actionability_label": str(primary_item.get("actionability_label") or "low"),
        "owner_hint": owner_hint,
        "member_count": len(items),
        "suppressed_member_count": sum(
            1 for item in items if not bool(item.get("is_primary_package_savings_owner", True))
        ),
        "primary_fingerprint": primary_item.get("fingerprint"),
        "fingerprints": [item.get("fingerprint") for item in items if item.get("fingerprint")],
        "services": services,
        "regions": regions,
        "check_ids": check_ids,
        "requires_approval": any(bool(item.get("requires_approval")) for item in items),
        "primary_recommendation": primary_item,
        "member_recommendations": items,
        "graph_package": graph_package,
    }


def _build_recommendation_packages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build ordered package-native recommendation objects from leaf items."""

    grouped_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ordered_keys: list[str] = []
    for item in items:
        package_key = _recommendation_package_group_key(item)
        if package_key not in grouped_items:
            ordered_keys.append(package_key)
        grouped_items[package_key].append(item)
    return [_build_recommendation_package(grouped_items[package_key]) for package_key in ordered_keys]


@recommendations_bp.route("/api/recommendations", methods=["GET"])
@require_permission("findings:read")
def api_recommendations() -> Any:
    """List actionable recommendations derived from current scoped findings.

    Query params:
        tenant_id (required): Tenant identifier
        workspace (required): Workspace identifier
        limit: Results limit (default 100)
        offset: Results offset (default 0)
        state, severity, service, check_id, category, region, account_id: Filters
        q: Substring match on title
        min_savings: Minimum monthly savings filter
        order: Sort order (savings_desc or detected_desc)

    Returns:
        Paginated list of recommendations
    """
    try:
        tenant_id, workspace = _require_scope_from_query()
        limit = _parse_int(_q("limit"), default=100, min_v=1, max_v=1000)
        offset = _parse_int(_q("offset"), default=0, min_v=0, max_v=5_000_000)
        view = (_q("view", "items") or "items").strip().lower()
        if view not in {"items", "packages"}:
            raise ValueError("view must be 'items' or 'packages'")

        order = (_q("order", "savings_desc") or "savings_desc").lower()
        if order not in {"savings_desc", "detected_desc"}:
            raise ValueError("order must be 'savings_desc' or 'detected_desc'")

        where, params = _build_recommendations_where(tenant_id, workspace)
        order_sql = (
            "estimated_monthly_savings DESC NULLS LAST, detected_at DESC, fingerprint"
            if order == "savings_desc"
            else "detected_at DESC, fingerprint"
        )

        base_sql = f"""
            SELECT
              fc.tenant_id, fc.workspace, fc.fingerprint, fc.check_id, fc.service, fc.severity,
              fc.category, fc.title, fc.estimated_monthly_savings, fc.region, fc.account_id,
              fc.detected_at, fc.effective_state, fc.payload,
              to_jsonb(r) AS run_meta
            FROM finding_current fc
            LEFT JOIN runs r
              ON r.tenant_id = fc.tenant_id
             AND r.workspace = fc.workspace
             AND r.run_id = fc.run_id
            WHERE {' AND '.join(where)}
            ORDER BY {order_sql}
        """
        sql = (
            base_sql
            if view == "packages"
            else f"{base_sql}\n            LIMIT %s OFFSET %s"
        )
        params2 = params if view == "packages" else params + [limit, offset]

        with db_conn() as conn:
            rows = fetch_all_dict_conn(conn, sql, params2)
            count_row = fetch_one_dict_conn(
                conn,
                f"SELECT COUNT(*) AS n FROM finding_current fc WHERE {' AND '.join(where)}",
                params,
            )
            graph_packages = _load_graph_packages_for_rows(
                conn,
                tenant_id=tenant_id,
                workspace=workspace,
                rows=rows,
            )

        items = [_build_recommendation_item(row, graph_packages=graph_packages) for row in rows]
        if view == "packages":
            packages = _build_recommendation_packages(items)
            paged_packages = packages[offset : offset + limit]
            return _ok(
                {
                    "tenant_id": tenant_id,
                    "workspace": workspace,
                    "view": view,
                    "limit": limit,
                    "offset": offset,
                    "total": len(packages),
                    "leaf_total": int((count_row or {}).get("n") or 0),
                    "items": paged_packages,
                }
            )
        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "view": view,
                "limit": limit,
                "offset": offset,
                "total": int((count_row or {}).get("n") or 0),
                "items": items,
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@recommendations_bp.route("/api/recommendations/composite", methods=["GET"])
@require_permission("findings:read")
def api_recommendations_composite() -> Any:
    """Aggregate recommendation opportunities for portfolio-level prioritization.

    Query params:
        tenant_id (required): Tenant identifier
        workspace (required): Workspace identifier
        limit: Results limit (default 100)
        offset: Results offset (default 0)
        group_by: Grouping key (recommendation_type, service, check_id, category, region)
        order: Sort order (savings_desc or count_desc)

    Returns:
        Aggregated recommendations by group
    """
    try:
        tenant_id, workspace = _require_scope_from_query()
        limit = _parse_int(_q("limit"), default=100, min_v=1, max_v=1000)
        offset = _parse_int(_q("offset"), default=0, min_v=0, max_v=5_000_000)

        group_by = (_q("group_by", "recommendation_type") or "recommendation_type").strip().lower()
        group_expr_map = {
            "recommendation_type": _recommendation_type_case_sql(),
            "service": "COALESCE(service, 'unknown')",
            "check_id": "check_id",
            "category": "COALESCE(category, 'unknown')",
            "region": "COALESCE(region, 'unknown')",
        }
        group_expr = group_expr_map.get(group_by)
        if not group_expr:
            raise ValueError("group_by must be one of: recommendation_type, service, check_id, category, region")

        order = (_q("order", "savings_desc") or "savings_desc").lower()
        if order not in {"savings_desc", "count_desc"}:
            raise ValueError("order must be 'savings_desc' or 'count_desc'")
        order_sql = "total_monthly_savings DESC NULLS LAST, finding_count DESC, group_key"
        if order == "count_desc":
            order_sql = "finding_count DESC, total_monthly_savings DESC NULLS LAST, group_key"

        where, params = _build_recommendations_where(tenant_id, workspace)
        sql = f"""
            SELECT
              {group_expr} AS group_key,
              COUNT(*)::bigint AS finding_count,
              SUM(COALESCE(estimated_monthly_savings, 0))::double precision AS total_monthly_savings,
              (SUM(COALESCE(estimated_monthly_savings, 0)) * 12.0)::double precision AS total_annual_savings
            FROM finding_current fc
            WHERE {' AND '.join(where)}
            GROUP BY group_key
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
        """
        params2 = params + [limit, offset]

        count_sql = f"""
            SELECT COUNT(*) AS n
            FROM (
              SELECT {group_expr} AS group_key
              FROM finding_current fc
              WHERE {' AND '.join(where)}
              GROUP BY group_key
            ) grouped
        """

        with db_conn() as conn:
            rows = fetch_all_dict_conn(conn, sql, params2)
            count_row = fetch_one_dict_conn(conn, count_sql, params)

        items = []
        for row in rows:
            items.append({
                "group_key": row.get("group_key"),
                "finding_count": int(row.get("finding_count") or 0),
                "total_monthly_savings": _as_float(row.get("total_monthly_savings"), default=0.0),
                "total_annual_savings": _as_float(row.get("total_annual_savings"), default=0.0),
            })

        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "group_by": group_by,
                "limit": limit,
                "offset": offset,
                "total": int((count_row or {}).get("n") or 0),
                "items": items,
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)


@recommendations_bp.route("/api/recommendations/estimate", methods=["POST"])
@recommendations_bp.route("/api/recommendations/preview", methods=["POST"])
@require_permission("findings:read")
def api_recommendations_estimate() -> Any:
    """Estimate cost/savings for a set of recommendations.

    JSON body:
        tenant_id, workspace, fingerprints: List of finding fingerprints to estimate
        limit, offset, order, state, severity, service, check_id, category, region, account_id, q, min_savings: Optional filters

    Returns:
        Cost/savings estimate for the recommendations
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
        tenant_id, workspace = _require_scope_from_json(payload)

        requested_fingerprints = _coerce_text_list(payload.get("fingerprints"), field_name="fingerprints")

        if "limit" in payload:
            limit = _coerce_positive_int(payload.get("limit"), field_name="limit")
            limit = min(limit, 1000)
        elif requested_fingerprints:
            limit = min(max(1, len(requested_fingerprints)), 1000)
        else:
            limit = 200

        if "offset" in payload:
            offset = _coerce_non_negative_int(payload.get("offset"), field_name="offset")
            offset = min(offset, 5_000_000)
        else:
            offset = 0

        order = str(payload.get("order") or "savings_desc").strip().lower()
        if order not in {"savings_desc", "detected_desc"}:
            raise ValueError("order must be 'savings_desc' or 'detected_desc'")

        where, params = _build_recommendations_where_from_values(
            tenant_id,
            workspace,
            effective_states=_coerce_text_list(payload.get("state", ["open"]), field_name="state"),
            severities=_coerce_text_list(payload.get("severity"), field_name="severity"),
            services=_coerce_text_list(payload.get("service"), field_name="service"),
            check_ids=_coerce_text_list(payload.get("check_id"), field_name="check_id"),
            categories=_coerce_text_list(payload.get("category"), field_name="category"),
            regions=_coerce_text_list(payload.get("region"), field_name="region"),
            account_ids=_coerce_text_list(payload.get("account_id"), field_name="account_id"),
            query_str=_coerce_optional_text(payload.get("q")),
            min_savings=_coerce_optional_float(payload.get("min_savings"), field_name="min_savings"),
            fingerprints=requested_fingerprints,
        )
        order_sql = (
            "estimated_monthly_savings DESC NULLS LAST, detected_at DESC, fingerprint"
            if order == "savings_desc"
            else "detected_at DESC, fingerprint"
        )

        sql = f"""
            SELECT
              fc.tenant_id, fc.workspace, fc.fingerprint, fc.check_id, fc.service, fc.severity,
              fc.category, fc.title, fc.estimated_monthly_savings, fc.region, fc.account_id,
              fc.detected_at, fc.effective_state, fc.payload,
              to_jsonb(r) AS run_meta
            FROM finding_current fc
            LEFT JOIN runs r
              ON r.tenant_id = fc.tenant_id
             AND r.workspace = fc.workspace
             AND r.run_id = fc.run_id
            WHERE {' AND '.join(where)}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
        """
        params2 = params + [limit, offset]

        with db_conn() as conn:
            rows = fetch_all_dict_conn(conn, sql, params2)
            count_row = fetch_one_dict_conn(
                conn,
                f"SELECT COUNT(*) AS n FROM finding_current fc WHERE {' AND '.join(where)}",
                params,
            )
            graph_packages = _load_graph_packages_for_rows(
                conn,
                tenant_id=tenant_id,
                workspace=workspace,
                rows=rows,
            )

        items = [_build_recommendation_item(row, graph_packages=graph_packages) for row in rows]
        total_monthly_savings = round(sum(_as_float(item.get("effective_estimated_monthly_savings")) for item in items), 2)
        total_annual_savings = round(total_monthly_savings * 12.0, 2)
        pricing_versions = sorted(
            {
                str(item.get("pricing_version")).strip()
                for item in items
                if str(item.get("pricing_version") or "").strip()
            }
        )
        pricing_version = "unknown"
        if len(pricing_versions) == 1:
            pricing_version = pricing_versions[0]
        elif len(pricing_versions) > 1:
            pricing_version = "mixed"

        avg_confidence = round(
            sum(int(item.get("confidence") or 0) for item in items) / max(1, len(items)),
            2,
        )
        risk_warnings = _build_estimate_risk_warnings(
            items=items,
            requested_fingerprints=requested_fingerprints,
        )

        return _ok(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "mode": "estimate",
                "pricing_version": pricing_version,
                "pricing_versions": pricing_versions,
                "limit": limit,
                "offset": offset,
                "total": int((count_row or {}).get("n") or 0),
                "selected_count": len(items),
                "totals": {
                    "estimated_monthly_savings": total_monthly_savings,
                    "estimated_annual_savings": total_annual_savings,
                    "average_confidence": avg_confidence,
                },
                "risk_warnings": risk_warnings,
                "items": items,
            }
        )
    except ValueError as exc:
        return _err("bad_request", str(exc), status=400)
