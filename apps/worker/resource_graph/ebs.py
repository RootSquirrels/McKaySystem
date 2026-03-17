"""EBS-focused graph builders."""

from __future__ import annotations

from typing import Any

from apps.worker.resource_graph.common import GraphBuildState, non_empty_text


def build_ebs_relationships(
    state: GraphBuildState,
    *,
    primary_key: str,
    account_id: str,
    region: str,
    service: str,
    resource_type: str,
    dimensions: dict[str, Any],
) -> None:
    """Derive EBS resource relationships from emitted dimensions."""
    if resource_type != "volume":
        return

    instance_id = non_empty_text(dimensions.get("instance_id"))
    if not instance_id:
        return

    instance_key, _instance_type, _instance_service = state.related_node_for_id(
        related_id=instance_id,
        account_id=account_id,
        region=region,
    )
    state.ensure_edge(
        from_resource_key=primary_key,
        to_resource_key=instance_key,
        edge_type="attached_to",
        service=service,
        account_id=account_id,
        region=region,
    )
