"""Kinesis Data Streams optimization checker."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, OperationNotPageableError

import checks.aws._common as common
from checks.aws._common import AwsAccountContext, build_scope, get_logger, money, safe_region_from_client
from checks.aws.defaults import (
    KINESIS_FALLBACK_EFO_CONSUMER_SHARD_HOURLY_USD,
    KINESIS_FALLBACK_SHARD_HOURLY_USD,
    KINESIS_LOOKBACK_DAYS,
    KINESIS_MAX_FINDINGS_PER_TYPE,
    KINESIS_MIN_DAILY_DATAPOINTS,
    KINESIS_PROVISIONED_UNDERUTILIZED_UTIL_THRESHOLD_PCT,
    KINESIS_RETENTION_LOW_TRAFFIC_P95_DAILY_GIB_THRESHOLD,
    KINESIS_RETENTION_REVIEW_MIN_HOURS,
    KINESIS_UNUSED_EFO_MAX_P95_OUTGOING_RECORDS,
)
from checks.registry import Bootstrap, register_checker
from contracts.finops_checker_pattern import Checker, FindingDraft, RunContext, Severity

_LOGGER = get_logger("kinesis_streams")
_NAMESPACE = "AWS/Kinesis"
_PERIOD_SECONDS = 86_400
_INGRESS_BYTES_PER_SHARD_DAY = 1.0 * 1024.0**2 * 86_400.0
_INGRESS_RECORDS_PER_SHARD_DAY = 1_000.0 * 86_400.0


@dataclass(frozen=True)
class KinesisStreamsConfig:
    """Configuration knobs for Kinesis Data Streams checks."""

    lookback_days: int = KINESIS_LOOKBACK_DAYS
    min_datapoints: int = KINESIS_MIN_DAILY_DATAPOINTS
    provisioned_underutilized_util_threshold_pct: float = KINESIS_PROVISIONED_UNDERUTILIZED_UTIL_THRESHOLD_PCT
    retention_review_min_hours: int = KINESIS_RETENTION_REVIEW_MIN_HOURS
    retention_low_traffic_p95_daily_gib_threshold: float = KINESIS_RETENTION_LOW_TRAFFIC_P95_DAILY_GIB_THRESHOLD
    unused_efo_max_p95_outgoing_records: float = KINESIS_UNUSED_EFO_MAX_P95_OUTGOING_RECORDS
    max_findings_per_type: int = KINESIS_MAX_FINDINGS_PER_TYPE
    fallback_shard_hourly_usd: float = KINESIS_FALLBACK_SHARD_HOURLY_USD
    fallback_efo_consumer_shard_hourly_usd: float = KINESIS_FALLBACK_EFO_CONSUMER_SHARD_HOURLY_USD


def _is_access_denied(exc: ClientError) -> bool:
    """Return whether a ClientError is an IAM-style access denial."""

    try:
        code = str(exc.response.get("Error", {}).get("Code") or "")
    except (AttributeError, TypeError, ValueError):
        return False
    return code in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
        "UnrecognizedClientException",
    }


def _to_int(value: Any) -> int:
    """Safely coerce a value to int, defaulting to 0."""

    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_text(value: Any) -> str:
    """Safely coerce a value to stripped text."""

    return str(value or "").strip()


def _p95(values: Sequence[float]) -> float | None:
    """Return p95 using deterministic floor percentile selection."""

    return common.percentile(values, 95.0, method="floor")


def _paginate_stream_names(client: Any) -> Iterator[str]:
    """Yield Kinesis stream names with paginator/fallback support."""

    if hasattr(client, "get_paginator"):
        try:
            paginator = client.get_paginator("list_streams")
            for page in paginator.paginate():
                for name in page.get("StreamNames", []) or []:
                    stream_name = str(name or "").strip()
                    if stream_name:
                        yield stream_name
            return
        except (OperationNotPageableError, AttributeError, KeyError, TypeError, ValueError):
            pass

    next_name: str | None = None
    while True:
        request: dict[str, Any] = {}
        if next_name:
            request["ExclusiveStartStreamName"] = next_name
        response = client.list_streams(**request) if request else client.list_streams()
        names = response.get("StreamNames", []) or []
        last_name = None
        for name in names:
            stream_name = str(name or "").strip()
            if stream_name:
                last_name = stream_name
                yield stream_name
        if not bool(response.get("HasMoreStreams")) or not last_name:
            break
        next_name = last_name


def _stream_mode(summary: Mapping[str, Any]) -> str:
    """Return the normalized Kinesis stream mode."""

    details = summary.get("StreamModeDetails") or {}
    if isinstance(details, Mapping):
        return str(details.get("StreamMode") or "PROVISIONED").upper()
    return "PROVISIONED"


def _active_consumers(consumers: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Return active consumer identities sorted deterministically."""

    items: list[dict[str, str]] = []
    for consumer in consumers:
        if str(consumer.get("ConsumerStatus") or "").upper() != "ACTIVE":
            continue
        name = str(consumer.get("ConsumerName") or "").strip()
        arn = str(consumer.get("ConsumerARN") or "").strip()
        if not name and not arn:
            continue
        items.append({"name": name, "arn": arn})
    return sorted(items, key=lambda item: (item["name"], item["arn"]))


def _combined_daily_gib(metrics: Mapping[str, float]) -> float:
    """Return p95 daily GiB combining incoming and outgoing stream bytes."""

    total_bytes = float(metrics.get("p95_incoming_bytes") or 0.0) + float(metrics.get("p95_outgoing_bytes") or 0.0)
    return total_bytes / float(1024.0**3)


def _csv_join(values: Sequence[str]) -> str:
    """Return a deterministic comma-separated list without empty values."""

    return ",".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def _suggested_shard_count(metrics: Mapping[str, float], *, open_shard_count: int) -> int:
    """Return a conservative suggested shard count for provisioned streams."""

    if open_shard_count <= 1:
        return 1
    ingress_shards = float(metrics.get("p95_incoming_bytes") or 0.0) / _INGRESS_BYTES_PER_SHARD_DAY
    record_shards = float(metrics.get("p95_incoming_records") or 0.0) / _INGRESS_RECORDS_PER_SHARD_DAY
    required = max(1, int(ceil(max(ingress_shards, record_shards, 0.0))))
    return min(open_shard_count, required)


class _KinesisCloudWatchMetrics:
    """Batch CloudWatch fetcher for Kinesis daily traffic metrics."""

    def __init__(self, cloudwatch: Any) -> None:
        self._cloudwatch = cloudwatch

    def fetch(
        self,
        *,
        stream_names: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, dict[str, float]]:
        """Return p95 daily traffic metrics keyed by stream name."""

        metrics = {
            "IncomingBytes": "p95_incoming_bytes",
            "OutgoingBytes": "p95_outgoing_bytes",
            "IncomingRecords": "p95_incoming_records",
            "OutgoingRecords": "p95_outgoing_records",
        }
        out: dict[str, dict[str, list[float]]] = {
            stream_name: {alias: [] for alias in metrics.values()}
            for stream_name in stream_names
        }

        queries: list[dict[str, Any]] = []
        query_to_target: dict[str, tuple[str, str]] = {}
        query_index = 0
        for stream_name in stream_names:
            for metric_name, alias in metrics.items():
                query_id = f"m{query_index}"
                query_index += 1
                query_to_target[query_id] = (stream_name, alias)
                queries.append(
                    {
                        "Id": query_id,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": _NAMESPACE,
                                "MetricName": metric_name,
                                "Dimensions": [{"Name": "StreamName", "Value": stream_name}],
                            },
                            "Period": _PERIOD_SECONDS,
                            "Stat": "Sum",
                        },
                        "ReturnData": True,
                    }
                )

        for start_index in range(0, len(queries), 200):
            batch = queries[start_index:start_index + 200]
            next_token: str | None = None
            while True:
                request: dict[str, Any] = {
                    "MetricDataQueries": batch,
                    "StartTime": start,
                    "EndTime": end,
                    "ScanBy": "TimestampAscending",
                    "MaxDatapoints": 5000,
                }
                if next_token:
                    request["NextToken"] = next_token
                response = self._cloudwatch.get_metric_data(**request)
                for row in response.get("MetricDataResults", []) or []:
                    if not isinstance(row, Mapping):
                        continue
                    query_id = str(row.get("Id") or "")
                    stream_name, alias = query_to_target.get(query_id, ("", ""))
                    if not stream_name or not alias:
                        continue
                    values = [common.safe_float(value) for value in (row.get("Values", []) or [])]
                    out[stream_name][alias].extend(values)
                next_token = _to_text(response.get("NextToken")) or None
                if not next_token:
                    break

        reduced: dict[str, dict[str, float]] = {}
        for stream_name, series_by_alias in out.items():
            reduced[stream_name] = {
                alias: float(_p95(values) or 0.0)
                for alias, values in series_by_alias.items()
            }
            reduced[stream_name]["datapoints"] = float(
                max(len(series_by_alias["p95_incoming_bytes"]), len(series_by_alias["p95_outgoing_bytes"]))
            )
        return reduced


class KinesisStreamsChecker(Checker):
    """Detect Kinesis Data Streams optimization opportunities."""

    checker_id = "aws.kinesis.streams.audit"

    def __init__(self, *, account: AwsAccountContext, cfg: KinesisStreamsConfig | None = None) -> None:
        self._account = account
        self._cfg = cfg or KinesisStreamsConfig()

    def run(self, ctx: RunContext) -> Iterable[FindingDraft]:
        """Run the checker for the current account and region."""

        services = getattr(ctx, "services", None)
        kinesis = getattr(services, "kinesis", None) if services is not None else None
        cloudwatch = getattr(services, "cloudwatch", None) if services is not None else None
        lambda_client = getattr(services, "lambda_client", None) if services is not None else None
        if kinesis is None or cloudwatch is None:
            return []

        region = safe_region_from_client(kinesis) or safe_region_from_client(cloudwatch)
        now_ts = common.now_utc()
        start = now_ts - timedelta(days=max(1, int(self._cfg.lookback_days)))
        emitted = 0

        try:
            stream_names = list(_paginate_stream_names(kinesis))
        except ClientError as exc:
            if _is_access_denied(exc):
                yield self._access_error(ctx, region=region, action="kinesis:ListStreams", resource_id="")
                return
            raise

        if not stream_names:
            return

        try:
            metrics_by_stream = _KinesisCloudWatchMetrics(cloudwatch).fetch(stream_names=stream_names, start=start, end=now_ts)
        except (BotoCoreError, ClientError) as exc:
            if isinstance(exc, ClientError) and _is_access_denied(exc):
                yield self._access_error(ctx, region=region, action="cloudwatch:GetMetricData", resource_id="")
                return
            raise

        for stream_name in stream_names:
            try:
                summary_response = kinesis.describe_stream_summary(StreamName=stream_name)
            except ClientError as exc:
                if _is_access_denied(exc):
                    if emitted < self._cfg.max_findings_per_type:
                        emitted += 1
                        yield self._access_error(
                            ctx,
                            region=region,
                            action="kinesis:DescribeStreamSummary",
                            resource_id=stream_name,
                        )
                    continue
                raise

            summary = (summary_response or {}).get("StreamDescriptionSummary") or {}
            if not isinstance(summary, Mapping):
                continue
            stream_arn = _to_text(summary.get("StreamARN"))
            stream_status = _to_text(summary.get("StreamStatus")).upper()
            if stream_status not in {"ACTIVE", "UPDATING"}:
                continue

            mode = _stream_mode(summary)
            open_shards = _to_int(summary.get("OpenShardCount"))
            retention_hours = _to_int(summary.get("RetentionPeriodHours"))
            active_consumers: list[dict[str, str]] = []
            try:
                consumer_response = kinesis.list_stream_consumers(StreamARN=stream_arn) if stream_arn else {"Consumers": []}
                consumer_rows = consumer_response.get("Consumers", []) or []
                if isinstance(consumer_rows, Sequence):
                    active_consumers = _active_consumers(
                        [consumer for consumer in consumer_rows if isinstance(consumer, Mapping)]
                    )
            except ClientError as exc:
                if _is_access_denied(exc):
                    if emitted < self._cfg.max_findings_per_type:
                        emitted += 1
                        yield self._access_error(
                            ctx,
                            region=region,
                            action="kinesis:ListStreamConsumers",
                            resource_id=stream_name,
                        )
                else:
                    raise

            metrics = metrics_by_stream.get(stream_name, {})
            datapoints = int(metrics.get("datapoints") or 0.0)
            if datapoints < int(self._cfg.min_datapoints):
                continue

            downstream = self._stream_downstream_relationships(
                lambda_client=lambda_client,
                stream_arn=stream_arn,
            )
            yield from self._stream_findings(
                ctx,
                region=region,
                stream_name=stream_name,
                stream_arn=stream_arn,
                mode=mode,
                open_shards=open_shards,
                retention_hours=retention_hours,
                active_consumers=active_consumers,
                downstream=downstream,
                metrics=metrics,
            )

    def _stream_findings(
        self,
        ctx: RunContext,
        *,
        region: str,
        stream_name: str,
        stream_arn: str,
        mode: str,
        open_shards: int,
        retention_hours: int,
        active_consumers: Sequence[Mapping[str, str]],
        downstream: Mapping[str, Sequence[str]],
        metrics: Mapping[str, float],
    ) -> Iterable[FindingDraft]:
        """Yield findings for one stream based on inventory and metrics."""

        consumer_names = [str(item.get("name") or "").strip() for item in active_consumers if str(item.get("name") or "").strip()]
        consumer_arns = [str(item.get("arn") or "").strip() for item in active_consumers if str(item.get("arn") or "").strip()]
        downstream_lambda_names = list(downstream.get("lambda_names") or [])
        downstream_lambda_arns = list(downstream.get("lambda_arns") or [])
        event_source_mapping_uuids = list(downstream.get("event_source_mapping_uuids") or [])

        combined_daily_gib = _combined_daily_gib(metrics)
        if mode == "PROVISIONED" and open_shards >= 2:
            util_pct = self._provisioned_utilization_pct(metrics=metrics, open_shard_count=open_shards)
            suggested_shards = _suggested_shard_count(metrics, open_shard_count=open_shards)
            reducible_shards = max(0, open_shards - suggested_shards)
            if (
                util_pct is not None
                and util_pct <= float(self._cfg.provisioned_underutilized_util_threshold_pct)
                and reducible_shards >= 1
            ):
                shard_monthly_cost = float(self._cfg.fallback_shard_hourly_usd) * 730.0
                yield FindingDraft(
                    check_id="aws.kinesis.stream.provisioned.overprovisioned",
                    check_name="Kinesis provisioned stream overprovisioned",
                    category="cost",
                    status="fail",
                    severity=Severity(level="medium", score=610),
                    title=f"Kinesis stream may be over-sharded: {stream_name}",
                    scope=build_scope(
                        ctx,
                        account=self._account,
                        region=region,
                        service="kinesis",
                        resource_type="stream",
                        resource_id=stream_name,
                        resource_arn=stream_arn,
                    ),
                    message=(
                        f"Provisioned stream has {open_shards} open shard(s) but observed p95 ingress utilization is "
                        f"{util_pct:.2f}% over the last {self._cfg.lookback_days} days."
                    ),
                    recommendation=(
                        f"Review whether this stream can be resharded from {open_shards} to about {suggested_shards} shard(s), "
                        "or moved to on-demand mode if traffic is highly variable and consistently low."
                    ),
                    estimated_monthly_cost=money(open_shards * shard_monthly_cost),
                    estimated_monthly_savings=money(reducible_shards * shard_monthly_cost),
                    estimate_confidence=45,
                    estimate_notes="Estimate uses fallback shard-hour pricing and conservative p95 daily ingress sizing.",
                    dimensions={
                        "stream_name": stream_name,
                        "stream_mode": mode,
                        "open_shard_count": str(open_shards),
                        "suggested_shard_count": str(suggested_shards),
                        "reducible_shard_count": str(reducible_shards),
                        "consumer_count": str(len(active_consumers)),
                        "consumer_names": ",".join(consumer_names),
                        "consumer_arns": ",".join(consumer_arns),
                        "downstream_lambda_names": _csv_join(downstream_lambda_names),
                        "downstream_lambda_arns": _csv_join(downstream_lambda_arns),
                        "event_source_mapping_uuids": _csv_join(event_source_mapping_uuids),
                        "p95_incoming_bytes": f"{float(metrics.get('p95_incoming_bytes') or 0.0):.2f}",
                        "p95_incoming_records": f"{float(metrics.get('p95_incoming_records') or 0.0):.2f}",
                        "p95_ingress_utilization_pct": f"{util_pct:.2f}",
                        "optimization_focus": "shard_count_review",
                    },
                    issue_key={"signal": "provisioned_overprovisioned", "stream_name": stream_name},
                )

        if (
            retention_hours >= int(self._cfg.retention_review_min_hours)
            and combined_daily_gib <= float(self._cfg.retention_low_traffic_p95_daily_gib_threshold)
        ):
            yield FindingDraft(
                check_id="aws.kinesis.stream.retention.extended.review",
                check_name="Kinesis stream extended retention review",
                category="cost",
                status="info",
                severity=Severity(level="low", score=320),
                title=f"Kinesis stream extended retention may be unnecessary: {stream_name}",
                scope=build_scope(
                    ctx,
                    account=self._account,
                    region=region,
                    service="kinesis",
                    resource_type="stream",
                    resource_id=stream_name,
                    resource_arn=stream_arn,
                ),
                message=(
                    f"Stream retention is set to {retention_hours} hour(s) while combined p95 daily traffic is only "
                    f"{combined_daily_gib:.2f} GiB over the last {self._cfg.lookback_days} days."
                ),
                recommendation=(
                    "Review whether this stream really needs extended retention beyond the default 24 hours. "
                    "If replay and recovery requirements are lighter than configured, reduce retention."
                ),
                dimensions={
                    "stream_name": stream_name,
                    "stream_mode": mode,
                    "retention_hours": str(retention_hours),
                    "consumer_count": str(len(active_consumers)),
                    "consumer_names": ",".join(consumer_names),
                    "consumer_arns": ",".join(consumer_arns),
                    "downstream_lambda_names": _csv_join(downstream_lambda_names),
                    "downstream_lambda_arns": _csv_join(downstream_lambda_arns),
                    "event_source_mapping_uuids": _csv_join(event_source_mapping_uuids),
                    "p95_combined_daily_gib": f"{combined_daily_gib:.2f}",
                    "optimization_focus": "retention_review",
                },
                issue_key={"signal": "extended_retention_review", "stream_name": stream_name},
            )

        p95_outgoing_records = float(metrics.get("p95_outgoing_records") or 0.0)
        if active_consumers and p95_outgoing_records <= float(self._cfg.unused_efo_max_p95_outgoing_records):
            consumer_monthly_cost = (
                len(active_consumers) * max(1, open_shards) * float(self._cfg.fallback_efo_consumer_shard_hourly_usd) * 730.0
            )
            yield FindingDraft(
                check_id="aws.kinesis.stream.enhanced_fanout.unused.review",
                check_name="Kinesis enhanced fan-out review",
                category="cost",
                status="info",
                severity=Severity(level="low", score=300),
                title=f"Kinesis enhanced fan-out consumers may be underused: {stream_name}",
                scope=build_scope(
                    ctx,
                    account=self._account,
                    region=region,
                    service="kinesis",
                    resource_type="stream",
                    resource_id=stream_name,
                    resource_arn=stream_arn,
                ),
                message=(
                    f"Stream has {len(consumer_names)} active enhanced fan-out consumer(s), but observed p95 outgoing "
                    f"records are only {p95_outgoing_records:.2f} per day over the last {self._cfg.lookback_days} days."
                ),
                recommendation=(
                    "Review whether all registered enhanced fan-out consumers are still needed. "
                    "Delete or consolidate idle consumers if standard polling or fewer consumers would meet latency needs."
                ),
                estimated_monthly_cost=money(consumer_monthly_cost),
                estimated_monthly_savings=money(consumer_monthly_cost),
                estimate_confidence=35,
                estimate_notes="Estimate uses fallback enhanced fan-out consumer-shard-hour pricing.",
                dimensions={
                    "stream_name": stream_name,
                    "consumer_count": str(len(active_consumers)),
                    "consumer_names": ",".join(consumer_names),
                    "consumer_arns": ",".join(consumer_arns),
                    "downstream_lambda_names": _csv_join(downstream_lambda_names),
                    "downstream_lambda_arns": _csv_join(downstream_lambda_arns),
                    "event_source_mapping_uuids": _csv_join(event_source_mapping_uuids),
                    "open_shard_count": str(max(1, open_shards)),
                    "p95_outgoing_records": f"{p95_outgoing_records:.2f}",
                    "optimization_focus": "delete_unused_consumers",
                },
                issue_key={"signal": "enhanced_fanout_review", "stream_name": stream_name},
            )

    def _stream_downstream_relationships(
        self,
        *,
        lambda_client: Any,
        stream_arn: str,
    ) -> dict[str, list[str]]:
        """Return downstream Lambda mappings for one stream best-effort."""

        result: dict[str, list[str]] = {
            "lambda_names": [],
            "lambda_arns": [],
            "event_source_mapping_uuids": [],
        }
        if lambda_client is None or not stream_arn:
            return result

        list_mappings = getattr(lambda_client, "list_event_source_mappings", None)
        if not callable(list_mappings):
            return result

        try:
            response = list_mappings(EventSourceArn=stream_arn)
        except ClientError:
            return result
        except BotoCoreError:
            return result

        mappings = response.get("EventSourceMappings", []) or []
        if not isinstance(mappings, Sequence):
            return result

        lambda_names: list[str] = []
        lambda_arns: list[str] = []
        mapping_uuids: list[str] = []
        for item in mappings:
            if not isinstance(item, Mapping):
                continue
            function_arn = _to_text(item.get("FunctionArn"))
            uuid = _to_text(item.get("UUID"))
            if function_arn:
                lambda_arns.append(function_arn)
                function_name = function_arn.rsplit(":", maxsplit=1)[-1].strip()
                if function_name:
                    lambda_names.append(function_name)
            if uuid:
                mapping_uuids.append(uuid)

        result["lambda_names"] = sorted(set(lambda_names))
        result["lambda_arns"] = sorted(set(lambda_arns))
        result["event_source_mapping_uuids"] = sorted(set(mapping_uuids))
        return result

    @staticmethod
    def _provisioned_utilization_pct(*, metrics: Mapping[str, float], open_shard_count: int) -> float | None:
        """Return conservative provisioned ingress utilization percentage."""

        if open_shard_count <= 0:
            return None
        incoming_bytes = float(metrics.get("p95_incoming_bytes") or 0.0)
        incoming_records = float(metrics.get("p95_incoming_records") or 0.0)
        byte_util = incoming_bytes / (float(open_shard_count) * _INGRESS_BYTES_PER_SHARD_DAY)
        record_util = incoming_records / (float(open_shard_count) * _INGRESS_RECORDS_PER_SHARD_DAY)
        return max(byte_util, record_util) * 100.0

    def _access_error(
        self,
        ctx: RunContext,
        *,
        region: str,
        action: str,
        resource_id: str,
    ) -> FindingDraft:
        """Build a standard Kinesis access-error finding."""

        return FindingDraft(
            check_id="aws.kinesis.access.error",
            check_name="Kinesis API access error",
            category="governance",
            status="info",
            severity=Severity(level="info", score=0),
            title="Unable to collect full Kinesis inventory due to IAM restrictions",
            scope=build_scope(
                ctx,
                account=self._account,
                region=region,
                service="kinesis",
                resource_type="stream",
                resource_id=resource_id or self._account.account_id,
            ),
            message=f"Access denied calling {action} in region '{region}'.",
            recommendation=(
                "Grant least-privilege read permissions for Kinesis Data Streams and CloudWatch metrics so the platform "
                "can evaluate stream utilization and consumer configuration."
            ),
            issue_key={
                "signal": "access_error",
                "service": "kinesis",
                "action": action,
                "region": region,
                "resource_id": resource_id,
            },
        )


@register_checker("checks.aws.kinesis_streams:KinesisStreamsChecker")
def _factory(ctx: RunContext, bootstrap: Bootstrap) -> KinesisStreamsChecker:
    """Build the Kinesis checker from bootstrap context."""

    account_id = str(bootstrap.get("aws_account_id") or "")
    if not account_id:
        raise RuntimeError("aws_account_id missing from bootstrap (required for KinesisStreamsChecker)")
    billing_account_id = str(bootstrap.get("aws_billing_account_id") or account_id)
    return KinesisStreamsChecker(
        account=AwsAccountContext(account_id=account_id, billing_account_id=billing_account_id),
    )
