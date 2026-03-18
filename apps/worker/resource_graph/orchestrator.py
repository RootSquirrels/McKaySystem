"""Orchestration entrypoint for explicit resource graph builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from apps.worker.resource_graph.common import (
    GraphBuildState,
    ResourceGraphEdge,
    ResourceGraphNode,
    as_record,
    infer_resource_kind,
    non_empty_text,
    normalize_resource_type,
    normalize_service,
)
from apps.worker.resource_graph.ebs import build_ebs_relationships
from apps.worker.resource_graph.ec2 import build_ec2_instance_relationships
from apps.worker.resource_graph.elb import build_elb_relationships
from apps.worker.resource_graph.network import build_network_relationships
from apps.worker.resource_graph.rds import build_rds_relationships


def build_graph_from_findings(
    findings: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
    workspace: str,
    run_id: str,
) -> tuple[list[ResourceGraphNode], list[ResourceGraphEdge]]:
    """Derive a deterministic resource graph from wire findings via explicit builders."""
    state = GraphBuildState(
        tenant_id=tenant_id,
        workspace=workspace,
        run_id=run_id,
        nodes={},
        edges={},
    )

    for item in findings:
        scope = as_record(item.get("scope")) or {}
        payload = as_record(item.get("payload")) or {}
        payload_dimensions = as_record(payload.get("dimensions")) or {}
        top_level_dimensions = as_record(item.get("dimensions")) or {}
        dimensions = {
            **top_level_dimensions,
            **payload_dimensions,
        }
        issue_key = as_record(item.get("issue_key")) or {}
        account_id = non_empty_text(scope.get("account_id")) or non_empty_text(item.get("account_id")) or ""
        if not account_id:
            continue
        region = non_empty_text(scope.get("region")) or non_empty_text(item.get("region")) or ""
        service = non_empty_text(scope.get("service")) or non_empty_text(item.get("service")) or "unknown"
        resource_id = non_empty_text(scope.get("resource_id"))
        resource_arn = non_empty_text(scope.get("resource_arn"))
        resource_type = normalize_resource_type(scope.get("resource_type"))

        if service == "elbv2":
            resource_arn = resource_arn or non_empty_text(issue_key.get("lb_arn"))
        if resource_type == "nat_gateway":
            resource_id = resource_id or non_empty_text(issue_key.get("nat_gateway_id"))
        if resource_type == "volume":
            resource_id = resource_id or non_empty_text(dimensions.get("volume_id"))

        if not resource_type or resource_type == "resource":
            inferred_type, inferred_service = infer_resource_kind(resource_id, resource_arn)
            resource_type = normalize_resource_type(inferred_type)
            if service == "unknown":
                service = inferred_service
        service = normalize_service(service, resource_type=resource_type)

        native_id = resource_arn or resource_id
        if not native_id:
            continue

        primary_key = state.ensure_node(
            account_id=account_id,
            region=region,
            service=service,
            resource_type=resource_type,
            native_id=native_id,
            resource_arn=resource_arn,
            resource_name=non_empty_text(payload.get("resource_name")) or non_empty_text(item.get("title")),
            attributes_json={
                "check_id": non_empty_text(item.get("check_id")),
                "severity": non_empty_text(item.get("severity")),
                "title": non_empty_text(item.get("title")),
            },
        )

        build_network_relationships(
            state,
            primary_key=primary_key,
            account_id=account_id,
            region=region,
            service=service,
            resource_type=resource_type,
            dimensions=dimensions,
        )
        build_ec2_instance_relationships(
            state,
            primary_key=primary_key,
            account_id=account_id,
            region=region,
            service=service,
            resource_type=resource_type,
            dimensions=dimensions,
        )
        build_ebs_relationships(
            state,
            primary_key=primary_key,
            account_id=account_id,
            region=region,
            service=service,
            resource_type=resource_type,
            dimensions=dimensions,
        )
        build_elb_relationships(
            state,
            primary_key=primary_key,
            account_id=account_id,
            region=region,
            service=service,
            resource_arn=resource_arn,
            issue_key=issue_key,
            dimensions=dimensions,
            resource_id=resource_id,
        )
        build_rds_relationships(
            state,
            primary_key=primary_key,
            account_id=account_id,
            region=region,
            service=service,
            resource_type=resource_type,
            dimensions=dimensions,
        )

    return (
        sorted(state.nodes.values(), key=lambda item: item.resource_key),
        sorted(state.edges.values(), key=lambda item: item.edge_key),
    )
