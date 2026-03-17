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
    def _csv_values(key: str) -> list[str]:
        raw_value = non_empty_text(dimensions.get(key))
        if not raw_value:
            return []
        return sorted({item.strip() for item in raw_value.split(",") if item.strip()})

    subnet_dimension_keys = ("subnet_id", "subnet_ids")
    for dim_key in subnet_dimension_keys:
        related_ids = [non_empty_text(dimensions.get(dim_key))] if dim_key == "subnet_id" else _csv_values(dim_key)
        for related_id in [value for value in related_ids if value]:
            related_key, related_type, _related_service = state.related_node_for_id(
                related_id=related_id,
                account_id=account_id,
                region=region,
            )
            state.ensure_edge(
                from_resource_key=primary_key,
                to_resource_key=related_key,
                edge_type="member_of",
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
    for dim_key, edge_type in (("vpc_id", "member_of"),):
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

    for routed_subnet_id in _csv_values("routed_subnet_ids"):
        routed_subnet_key, _routed_subnet_type, _routed_subnet_service = state.related_node_for_id(
            related_id=routed_subnet_id,
            account_id=account_id,
            region=region,
        )
        state.ensure_edge(
            from_resource_key=routed_subnet_key,
            to_resource_key=primary_key,
            edge_type="routes_via",
            service=service,
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": "routed_subnet_ids"},
        )
