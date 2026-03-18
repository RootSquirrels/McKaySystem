"""Kinesis-focused graph builders."""

from __future__ import annotations

from typing import Any

from apps.worker.resource_graph.common import GraphBuildState, non_empty_text


def build_kinesis_relationships(
    state: GraphBuildState,
    *,
    primary_key: str,
    account_id: str,
    region: str,
    service: str,
    resource_type: str,
    dimensions: dict[str, Any],
) -> None:
    """Derive deterministic Kinesis downstream relationships from finding dimensions."""

    if service != "kinesis" or resource_type != "stream":
        return

    raw_consumer_arns = non_empty_text(dimensions.get("consumer_arns"))
    if raw_consumer_arns:
        consumer_names = {
            item.strip()
            for item in str(non_empty_text(dimensions.get("consumer_names")) or "").split(",")
            if item.strip()
        }
        for consumer_arn in sorted({item.strip() for item in raw_consumer_arns.split(",") if item.strip()}):
            consumer_name = None
            if consumer_arn.startswith("arn:") and "/consumer/" in consumer_arn:
                suffix = consumer_arn.rsplit("/consumer/", maxsplit=1)[-1]
                parsed_name = suffix.split(":", maxsplit=1)[0].strip()
                if parsed_name:
                    consumer_name = parsed_name
            if consumer_name is None and consumer_names:
                consumer_name = sorted(consumer_names)[0]
            consumer_key = state.ensure_node(
                account_id=account_id,
                region=region,
                service="kinesis",
                resource_type="kinesis_consumer",
                native_id=consumer_arn,
                resource_arn=consumer_arn,
                resource_name=consumer_name,
            )
            state.ensure_edge(
                from_resource_key=primary_key,
                to_resource_key=consumer_key,
                edge_type="consumed_by",
                service="kinesis",
                account_id=account_id,
                region=region,
                attributes_json={"dimension_key": "consumer_arns"},
            )

    raw_lambda_arns = non_empty_text(dimensions.get("downstream_lambda_arns"))
    lambda_names = {
        item.strip()
        for item in str(non_empty_text(dimensions.get("downstream_lambda_names")) or "").split(",")
        if item.strip()
    }
    mapping_uuids = [
        item.strip()
        for item in str(non_empty_text(dimensions.get("event_source_mapping_uuids")) or "").split(",")
        if item.strip()
    ]
    if not raw_lambda_arns:
        return

    lambda_arns = sorted({item.strip() for item in raw_lambda_arns.split(",") if item.strip()})
    for index, lambda_arn in enumerate(lambda_arns):
        lambda_name = None
        if lambda_arn.startswith("arn:") and ":function:" in lambda_arn:
            parsed_name = lambda_arn.rsplit(":function:", maxsplit=1)[-1].strip()
            if parsed_name:
                lambda_name = parsed_name
        if lambda_name is None and lambda_names:
            lambda_name = sorted(lambda_names)[0]
        lambda_key = state.ensure_node(
            account_id=account_id,
            region=region,
            service="lambda",
            resource_type="function",
            native_id=lambda_arn,
            resource_arn=lambda_arn,
            resource_name=lambda_name,
        )
        mapping_uuid = mapping_uuids[index] if index < len(mapping_uuids) else None
        if mapping_uuid:
            mapping_key = state.ensure_node(
                account_id=account_id,
                region=region,
                service="lambda",
                resource_type="event_source_mapping",
                native_id=mapping_uuid,
                resource_name=mapping_uuid,
                attributes_json={
                    "source_stream_key": primary_key,
                    "target_lambda_arn": lambda_arn,
                },
            )
            state.ensure_edge(
                from_resource_key=primary_key,
                to_resource_key=mapping_key,
                edge_type="feeds_via_mapping",
                service="kinesis",
                account_id=account_id,
                region=region,
                attributes_json={"dimension_key": "event_source_mapping_uuids"},
            )
            state.ensure_edge(
                from_resource_key=mapping_key,
                to_resource_key=lambda_key,
                edge_type="invokes",
                service="lambda",
                account_id=account_id,
                region=region,
                attributes_json={"source_stream_key": primary_key},
            )
            continue

        state.ensure_edge(
            from_resource_key=primary_key,
            to_resource_key=lambda_key,
            edge_type="feeds",
            service="kinesis",
            account_id=account_id,
            region=region,
            attributes_json={"dimension_key": "downstream_lambda_arns"},
        )
