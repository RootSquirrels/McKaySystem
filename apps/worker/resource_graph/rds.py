"""RDS-focused graph builders."""

from __future__ import annotations

from typing import Any

from apps.worker.resource_graph.common import GraphBuildState, non_empty_text


def build_rds_relationships(
    state: GraphBuildState,
    *,
    primary_key: str,
    account_id: str,
    region: str,
    service: str,
    resource_type: str,
    dimensions: dict[str, Any],
) -> None:
    """Derive deterministic RDS topology relationships from finding dimensions."""

    def _csv_values(key: str) -> list[str]:
        raw_value = non_empty_text(dimensions.get(key))
        if not raw_value:
            return []
        return sorted({item.strip() for item in raw_value.split(",") if item.strip()})

    if service != "rds" or resource_type != "db_instance":
        return

    subnet_group_name = non_empty_text(dimensions.get("db_subnet_group"))
    if subnet_group_name:
        subnet_group_key = state.ensure_node(
            account_id=account_id,
            region=region,
            service="rds",
            resource_type="db_subnet_group",
            native_id=subnet_group_name,
            resource_name=subnet_group_name,
        )
        state.ensure_edge(
            from_resource_key=primary_key,
            to_resource_key=subnet_group_key,
            edge_type="member_of",
            service=service,
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": "db_subnet_group"},
        )

    vpc_id = non_empty_text(dimensions.get("vpc_id"))
    if vpc_id:
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

    subnet_ids = _csv_values("subnet_ids")
    for subnet_id in subnet_ids:
        subnet_key, _subnet_type, _subnet_service = state.related_node_for_id(
            related_id=subnet_id,
            account_id=account_id,
            region=region,
        )
        state.ensure_edge(
            from_resource_key=primary_key,
            to_resource_key=subnet_key,
            edge_type="deployed_in",
            service=service,
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": "subnet_ids"},
        )
        if vpc_id:
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

    for security_group_id in _csv_values("security_group_ids"):
        sg_key, _sg_type, _sg_service = state.related_node_for_id(
            related_id=security_group_id,
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
            attributes_json={"dimension_key": "security_group_ids"},
        )

    replica_source = non_empty_text(dimensions.get("replica_source"))
    if replica_source:
        source_key = state.ensure_node(
            account_id=account_id,
            region=region,
            service="rds",
            resource_type="db_instance",
            native_id=replica_source,
            resource_name=replica_source,
        )
        state.ensure_edge(
            from_resource_key=primary_key,
            to_resource_key=source_key,
            edge_type="replicates_from",
            service=service,
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": "replica_source"},
        )

    cluster_id = non_empty_text(dimensions.get("db_cluster_identifier"))
    if not cluster_id:
        return

    cluster_key = state.ensure_node(
        account_id=account_id,
        region=region,
        service="rds",
        resource_type="db_cluster",
        native_id=cluster_id,
        resource_name=cluster_id,
    )
    state.ensure_edge(
        from_resource_key=primary_key,
        to_resource_key=cluster_key,
        edge_type="member_of",
        service=service,
        account_id=account_id,
        region=region,
        attributes_json={"dimension_key": "db_cluster_identifier"},
    )
