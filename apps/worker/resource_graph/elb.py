"""ELB-focused graph builders."""

from __future__ import annotations

from typing import Any

from apps.worker.resource_graph.common import GraphBuildState, non_empty_text


def build_elb_relationships(
    state: GraphBuildState,
    *,
    primary_key: str,
    account_id: str,
    region: str,
    service: str,
    resource_arn: str | None,
    issue_key: dict[str, Any],
    dimensions: dict[str, Any],
    resource_id: str | None,
) -> None:
    """Derive load balancer and target group relationships from emitted fields."""
    def _csv_values(key: str) -> list[str]:
        raw_value = non_empty_text(dimensions.get(key))
        if not raw_value:
            return []
        return sorted({item.strip() for item in raw_value.split(",") if item.strip()})

    if service != "elbv2":
        return

    load_balancer_arn = resource_arn or non_empty_text(issue_key.get("lb_arn"))
    if load_balancer_arn:
        load_balancer_key = state.ensure_node(
            account_id=account_id,
            region=region,
            service="elbv2",
            resource_type="load_balancer",
            native_id=load_balancer_arn,
            resource_arn=load_balancer_arn,
            resource_name=non_empty_text(resource_id),
        )
        if load_balancer_key != primary_key:
            state.ensure_edge(
                from_resource_key=primary_key,
                to_resource_key=load_balancer_key,
                edge_type="identified_by",
                service="elbv2",
                account_id=account_id,
                region=region,
                attributes_json={"source": "issue_key.lb_arn"},
            )

    for tg_key_name in ("target_group_arn", "target_group_arns"):
        raw_tg_value = dimensions.get(tg_key_name)
        tg_values: list[str] = []
        if isinstance(raw_tg_value, list):
            tg_values = [non_empty_text(item) or "" for item in raw_tg_value]
        else:
            single_value = non_empty_text(raw_tg_value)
            if single_value:
                tg_values = _csv_values(tg_key_name) if tg_key_name.endswith("_arns") else [single_value]
        for tg_arn in [value for value in tg_values if value]:
            tg_key = state.ensure_node(
                account_id=account_id,
                region=region,
                service="elbv2",
                resource_type="target_group",
                native_id=tg_arn,
                resource_arn=tg_arn,
            )
            state.ensure_edge(
                from_resource_key=primary_key,
                to_resource_key=tg_key,
                edge_type="routes_to",
                service="elbv2",
                account_id=account_id,
                region=region,
                attributes_json={"dimension_key": tg_key_name},
            )
