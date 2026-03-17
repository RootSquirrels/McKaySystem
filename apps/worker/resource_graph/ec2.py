"""EC2-focused graph builders."""

from __future__ import annotations

from typing import Any

from apps.worker.resource_graph.common import GraphBuildState, non_empty_text


def build_ec2_instance_relationships(
    state: GraphBuildState,
    *,
    primary_key: str,
    account_id: str,
    region: str,
    service: str,
    resource_type: str,
    dimensions: dict[str, Any],
) -> None:
    """Derive EC2 instance relationships from emitted dimensions."""
    def _csv_values(key: str) -> list[str]:
        raw_value = non_empty_text(dimensions.get(key))
        if not raw_value:
            return []
        return sorted({item.strip() for item in raw_value.split(",") if item.strip()})

    if resource_type != "instance":
        if resource_type == "security_group":
            vpc_id = non_empty_text(dimensions.get("vpc_id"))
            if not vpc_id:
                return
            vpc_key, _vpc_type, _vpc_service = state.related_node_for_id(
                related_id=vpc_id,
                account_id=account_id,
                region=region,
            )
            state.ensure_edge(
                from_resource_key=primary_key,
                to_resource_key=vpc_key,
                edge_type="member_of",
                service=service,
                account_id=account_id,
                region=region,
                attributes_json={"dimension_key": "vpc_id"},
            )
        return

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

    security_group_ids = _csv_values("security_group_ids")
    offending_sg_ids = _csv_values("offending_sg_ids")
    for group_id in security_group_ids + [item for item in offending_sg_ids if item not in security_group_ids]:
        sg_key, _sg_type, _sg_service = state.related_node_for_id(
            related_id=group_id,
            account_id=account_id,
            region=region,
        )
        state.ensure_edge(
            from_resource_key=primary_key,
            to_resource_key=sg_key,
            edge_type="secured_by",
            service=service,
            account_id=account_id,
            region=region,
            attributes_json={
                "dimension_key": "offending_sg_ids" if group_id in offending_sg_ids else "security_group_ids"
            },
        )

    for volume_id in _csv_values("attached_volume_ids"):
        volume_key, _volume_type, _volume_service = state.related_node_for_id(
            related_id=volume_id,
            account_id=account_id,
            region=region,
        )
        state.ensure_edge(
            from_resource_key=volume_key,
            to_resource_key=primary_key,
            edge_type="attached_to",
            service="ec2",
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": "attached_volume_ids"},
        )
