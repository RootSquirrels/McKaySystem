"""Networking-focused graph builders."""

from __future__ import annotations

from typing import Any

from apps.worker.resource_graph.common import GraphBuildState, non_empty_text


def build_network_relationships(
    state: GraphBuildState,
    *,
    primary_key: str,
    account_id: str,
    region: str,
    service: str,
    resource_type: str,
    dimensions: dict[str, Any],
) -> None:
    """Derive VPC, subnet, and NAT relationships from emitted dimensions."""
    for dim_key, edge_type in (("subnet_id", "member_of"), ("vpc_id", "member_of")):
        related_id = non_empty_text(dimensions.get(dim_key))
        if not related_id:
            continue
        related_key, related_type, _related_service = state.related_node_for_id(
            related_id=related_id,
            account_id=account_id,
            region=region,
        )
        state.ensure_edge(
            from_resource_key=primary_key,
            to_resource_key=related_key,
            edge_type=edge_type,
            service=service,
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": dim_key},
        )
        if related_type == "subnet":
            vpc_id = non_empty_text(dimensions.get("vpc_id"))
            if vpc_id:
                vpc_key, _vpc_type, _vpc_service = state.related_node_for_id(
                    related_id=vpc_id,
                    account_id=account_id,
                    region=region,
                )
                state.ensure_edge(
                    from_resource_key=related_key,
                    to_resource_key=vpc_key,
                    edge_type="member_of",
                    service="vpc",
                    account_id=account_id,
                    region=region,
                    attributes_json={"dimension_key": "vpc_id"},
                )

    if resource_type != "nat_gateway":
        return

    subnet_id = non_empty_text(dimensions.get("subnet_id"))
    if not subnet_id:
        return

    subnet_key, _subnet_type, _subnet_service = state.related_node_for_id(
        related_id=subnet_id,
        account_id=account_id,
        region=region,
    )
    state.ensure_edge(
        from_resource_key=primary_key,
        to_resource_key=subnet_key,
        edge_type="attached_to",
        service=service,
        account_id=account_id,
        region=region,
        attributes_json={"dimension_key": "subnet_id"},
    )
    vpc_id = non_empty_text(dimensions.get("vpc_id"))
    if not vpc_id:
        return

    vpc_key, _vpc_type, _vpc_service = state.related_node_for_id(
        related_id=vpc_id,
        account_id=account_id,
        region=region,
    )
    state.ensure_edge(
        from_resource_key=subnet_key,
        to_resource_key=vpc_key,
        edge_type="member_of",
        service="vpc",
        account_id=account_id,
        region=region,
        attributes_json={"dimension_key": "vpc_id"},
    )
