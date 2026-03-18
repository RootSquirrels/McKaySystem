"""Unit tests for the Kinesis Data Streams checker."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, cast

from botocore.exceptions import ClientError

from checks.aws.kinesis_streams import KinesisStreamsChecker
from contracts.finops_checker_pattern import RunContext


class FakePaginator:
    """Minimal paginator fake."""

    def __init__(self, pages: List[Mapping[str, Any]]) -> None:
        self._pages = pages

    def paginate(self, **_kwargs: Any) -> Iterable[Mapping[str, Any]]:
        yield from self._pages


class FakeKinesis:
    """Minimal Kinesis Data Streams fake."""

    def __init__(
        self,
        *,
        region: str,
        stream_names: List[str],
        summaries_by_name: Dict[str, Mapping[str, Any]],
        consumers_by_arn: Optional[Dict[str, List[Mapping[str, Any]]]] = None,
        raise_on: Optional[str] = None,
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self._stream_names = stream_names
        self._summaries_by_name = summaries_by_name
        self._consumers_by_arn = consumers_by_arn or {}
        self._raise_on = raise_on

    def get_paginator(self, op_name: str) -> FakePaginator:
        if self._raise_on == op_name:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                op_name,
            )
        if op_name == "list_streams":
            return FakePaginator([{"StreamNames": list(self._stream_names)}])
        raise KeyError(op_name)

    def describe_stream_summary(self, *, StreamName: str) -> Mapping[str, Any]:
        if self._raise_on == "describe_stream_summary":
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "describe_stream_summary",
            )
        return {"StreamDescriptionSummary": dict(self._summaries_by_name.get(StreamName, {}))}

    def list_stream_consumers(self, *, StreamARN: str) -> Mapping[str, Any]:
        if self._raise_on == "list_stream_consumers":
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "list_stream_consumers",
            )
        return {"Consumers": list(self._consumers_by_arn.get(StreamARN, []))}


class FakeCloudWatch:
    """Minimal CloudWatch fake for Kinesis metrics."""

    def __init__(self, *, metrics_by_key: Dict[tuple[str, str], List[float]], raise_access_denied: bool = False) -> None:
        self.meta = SimpleNamespace(region_name="eu-west-1")
        self._metrics_by_key = metrics_by_key
        self._raise_access_denied = raise_access_denied

    def get_metric_data(self, *, MetricDataQueries: List[Mapping[str, Any]], **_kwargs: Any) -> Mapping[str, Any]:
        if self._raise_access_denied:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "get_metric_data",
            )
        results: list[dict[str, Any]] = []
        for query in MetricDataQueries:
            query_id = str(query.get("Id") or "")
            metric = ((query.get("MetricStat") or {}).get("Metric") or {})
            metric_name = str(metric.get("MetricName") or "")
            dimensions = metric.get("Dimensions") or []
            stream_name = ""
            for dimension in dimensions:
                if isinstance(dimension, Mapping) and dimension.get("Name") == "StreamName":
                    stream_name = str(dimension.get("Value") or "")
                    break
            results.append(
                {"Id": query_id, "Values": list(self._metrics_by_key.get((stream_name, metric_name), []))}
            )
        return {"MetricDataResults": results}


class FakeLambdaMappings:
    """Minimal Lambda client fake for event source mappings."""

    def __init__(self, *, mappings_by_event_source_arn: Dict[str, List[Mapping[str, Any]]]) -> None:
        self._mappings_by_event_source_arn = mappings_by_event_source_arn

    def list_event_source_mappings(self, *, EventSourceArn: str) -> Mapping[str, Any]:
        return {"EventSourceMappings": list(self._mappings_by_event_source_arn.get(EventSourceArn, []))}


def _mk_ctx(*, kinesis: Any, cloudwatch: Any, lambda_client: Any | None = None, region: str = "eu-west-1") -> RunContext:
    return cast(
        RunContext,
        SimpleNamespace(
            cloud="aws",
            services=SimpleNamespace(region=region, kinesis=kinesis, cloudwatch=cloudwatch, lambda_client=lambda_client),
        ),
    )


def _checker() -> KinesisStreamsChecker:
    import checks.aws.kinesis_streams as mod

    return KinesisStreamsChecker(
        account=mod.AwsAccountContext(account_id="111111111111", billing_account_id="111111111111")
    )


def test_overprovisioned_provisioned_stream_emits() -> None:
    """Provisioned streams with very low ingress should emit a shard review finding."""

    stream_arn = "arn:aws:kinesis:eu-west-1:111111111111:stream/orders"
    kinesis = FakeKinesis(
        region="eu-west-1",
        stream_names=["orders"],
        summaries_by_name={
            "orders": {
                "StreamName": "orders",
                "StreamARN": stream_arn,
                "StreamStatus": "ACTIVE",
                "OpenShardCount": 4,
                "RetentionPeriodHours": 24,
                "StreamModeDetails": {"StreamMode": "PROVISIONED"},
            }
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("orders", "IncomingBytes"): [10_000_000.0] * 7,
            ("orders", "OutgoingBytes"): [5_000_000.0] * 7,
            ("orders", "IncomingRecords"): [20_000.0] * 7,
            ("orders", "OutgoingRecords"): [10_000.0] * 7,
        }
    )

    lambda_client = FakeLambdaMappings(
        mappings_by_event_source_arn={
            stream_arn: [
                {
                    "UUID": "esm-1",
                    "FunctionArn": "arn:aws:lambda:eu-west-1:111111111111:function:orders-consumer",
                }
            ]
        }
    )

    findings = list(_checker().run(_mk_ctx(kinesis=kinesis, cloudwatch=cloudwatch, lambda_client=lambda_client)))
    hit = next(finding for finding in findings if finding.check_id == "aws.kinesis.stream.provisioned.overprovisioned")
    assert hit.scope.resource_id == "orders"
    assert hit.dimensions["suggested_shard_count"] == "1"
    assert hit.dimensions["downstream_lambda_names"] == "orders-consumer"
    assert hit.dimensions["optimization_focus"] == "shard_count_review"


def test_extended_retention_review_emits_for_low_traffic_stream() -> None:
    """Low-traffic streams with long retention should emit a retention review."""

    stream_arn = "arn:aws:kinesis:eu-west-1:111111111111:stream/audit"
    kinesis = FakeKinesis(
        region="eu-west-1",
        stream_names=["audit"],
        summaries_by_name={
            "audit": {
                "StreamName": "audit",
                "StreamARN": stream_arn,
                "StreamStatus": "ACTIVE",
                "OpenShardCount": 1,
                "RetentionPeriodHours": 336,
                "StreamModeDetails": {"StreamMode": "ON_DEMAND"},
            }
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("audit", "IncomingBytes"): [1_000_000_000.0] * 7,
            ("audit", "OutgoingBytes"): [1_000_000_000.0] * 7,
            ("audit", "IncomingRecords"): [50_000.0] * 7,
            ("audit", "OutgoingRecords"): [50_000.0] * 7,
        }
    )

    findings = list(_checker().run(_mk_ctx(kinesis=kinesis, cloudwatch=cloudwatch)))
    hit = next(finding for finding in findings if finding.check_id == "aws.kinesis.stream.retention.extended.review")
    assert hit.scope.resource_id == "audit"
    assert hit.dimensions["optimization_focus"] == "retention_review"


def test_unused_enhanced_fanout_review_emits() -> None:
    """Streams with active enhanced fan-out consumers and near-zero egress should emit a review."""

    stream_arn = "arn:aws:kinesis:eu-west-1:111111111111:stream/events"
    kinesis = FakeKinesis(
        region="eu-west-1",
        stream_names=["events"],
        summaries_by_name={
            "events": {
                "StreamName": "events",
                "StreamARN": stream_arn,
                "StreamStatus": "ACTIVE",
                "OpenShardCount": 2,
                "RetentionPeriodHours": 24,
                "StreamModeDetails": {"StreamMode": "PROVISIONED"},
            }
        },
        consumers_by_arn={
            stream_arn: [
                {
                    "ConsumerName": "analytics-a",
                    "ConsumerARN": f"{stream_arn}/consumer/analytics-a:123",
                    "ConsumerStatus": "ACTIVE",
                },
                {
                    "ConsumerName": "analytics-b",
                    "ConsumerARN": f"{stream_arn}/consumer/analytics-b:456",
                    "ConsumerStatus": "ACTIVE",
                },
            ]
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("events", "IncomingBytes"): [2_000_000.0] * 7,
            ("events", "OutgoingBytes"): [1_000_000.0] * 7,
            ("events", "IncomingRecords"): [200.0] * 7,
            ("events", "OutgoingRecords"): [10.0] * 7,
        }
    )

    findings = list(_checker().run(_mk_ctx(kinesis=kinesis, cloudwatch=cloudwatch)))
    hit = next(finding for finding in findings if finding.check_id == "aws.kinesis.stream.enhanced_fanout.unused.review")
    assert hit.scope.resource_id == "events"
    assert hit.dimensions["consumer_count"] == "2"
    assert "analytics-a" in hit.dimensions["consumer_names"]
    assert "consumer/analytics-a:" in hit.dimensions["consumer_arns"]
    assert hit.dimensions["optimization_focus"] == "delete_unused_consumers"


def test_access_denied_emits_info_finding() -> None:
    """Access denied on stream listing should emit one info finding and stop."""

    kinesis = FakeKinesis(
        region="eu-west-1",
        stream_names=[],
        summaries_by_name={},
        raise_on="list_streams",
    )
    cloudwatch = FakeCloudWatch(metrics_by_key={})

    findings = list(_checker().run(_mk_ctx(kinesis=kinesis, cloudwatch=cloudwatch)))
    assert len(findings) == 1
    assert findings[0].check_id == "aws.kinesis.access.error"
    assert findings[0].status == "info"
