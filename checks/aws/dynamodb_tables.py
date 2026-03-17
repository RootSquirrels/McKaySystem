"""DynamoDB provisioned-capacity optimization checker."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, OperationNotPageableError

import checks.aws._common as common
from checks.aws._common import AwsAccountContext, build_scope, get_logger, safe_region_from_client
from checks.aws.defaults import (
    DYNAMODB_LOOKBACK_DAYS,
    DYNAMODB_MAX_FINDINGS_PER_TYPE,
    DYNAMODB_MIN_DATAPOINTS,
    DYNAMODB_UNDERUTILIZED_UTIL_THRESHOLD_PCT,
)
from checks.registry import Bootstrap, register_checker
from contracts.finops_checker_pattern import Checker, FindingDraft, RunContext, Severity

_LOGGER = get_logger("dynamodb_tables")


@dataclass(frozen=True)
class DynamoDbTablesConfig:
    """Configuration knobs for DynamoDbTablesChecker."""

    lookback_days: int = DYNAMODB_LOOKBACK_DAYS
    min_datapoints: int = DYNAMODB_MIN_DATAPOINTS
    underutilized_util_threshold_pct: float = DYNAMODB_UNDERUTILIZED_UTIL_THRESHOLD_PCT
    max_findings_per_type: int = DYNAMODB_MAX_FINDINGS_PER_TYPE


def _is_access_denied(exc: ClientError) -> bool:
    try:
        code = str(exc.response.get("Error", {}).get("Code") or "")
    except (TypeError, ValueError, AttributeError):
        return False
    return code in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
        "UnrecognizedClientException",
    }


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _paginate_strings(
    client: Any,
    operation: str,
    result_key: str,
    *,
    params: dict[str, Any] | None = None,
    request_token_key: str = "ExclusiveStartTableName",
    response_token_keys: Sequence[str] = ("LastEvaluatedTableName",),
) -> Iterator[str]:
    call_params = dict(params or {})

    if hasattr(client, "get_paginator"):
        try:
            paginator = client.get_paginator(operation)
            for page in paginator.paginate(**call_params):
                for item in page.get(result_key, []) or []:
                    text = str(item or "").strip()
                    if text:
                        yield text
            return
        except (OperationNotPageableError, AttributeError, KeyError, TypeError, ValueError):
            pass

    call = getattr(client, operation, None)
    if not callable(call):
        raise AttributeError(f"client has no operation {operation}")

    next_token: str | None = None
    while True:
        req = dict(call_params)
        if next_token:
            req[request_token_key] = next_token
        resp = call(**req) if req else call()
        for item in resp.get(result_key, []) or []:
            text = str(item or "").strip()
            if text:
                yield text

        next_token = None
        for key in response_token_keys:
            token = resp.get(key)
            if token:
                next_token = str(token)
                break
        if not next_token:
            break


def _metric_query(query_id: str, *, dimensions: list[dict[str, str]], metric_name: str) -> dict[str, Any]:
    return {
        "Id": query_id,
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/DynamoDB",
                "MetricName": metric_name,
                "Dimensions": dimensions,
            },
            "Period": 86_400,
            "Stat": "Maximum",
        },
        "ReturnData": True,
    }


def _p95(values: Sequence[float]) -> float | None:
    return common.percentile(values, 95.0, method="floor")


class DynamoDbTablesChecker(Checker):
    """Detect provisioned DynamoDB tables and GSIs with low observed utilization."""

    checker_id = "aws.dynamodb.tables.audit"

    def __init__(
        self,
        *,
        account: AwsAccountContext,
        cfg: DynamoDbTablesConfig | None = None,
    ) -> None:
        self._account = account
        self._cfg = cfg or DynamoDbTablesConfig()

    def run(self, ctx: RunContext) -> Iterable[FindingDraft]:
        _LOGGER.info("Starting DynamoDB tables check")
        services = getattr(ctx, "services", None)
        dynamodb = getattr(services, "dynamodb", None) if services is not None else None
        cloudwatch = getattr(services, "cloudwatch", None) if services is not None else None
        if dynamodb is None or cloudwatch is None:
            return []

        region = safe_region_from_client(dynamodb) or safe_region_from_client(cloudwatch)
        now_ts = common.now_utc()
        start = now_ts - timedelta(days=max(1, int(self._cfg.lookback_days)))
        emitted = 0

        try:
            table_names = list(_paginate_strings(dynamodb, "list_tables", "TableNames"))
        except ClientError as exc:
            if _is_access_denied(exc):
                yield self._access_error(ctx, region=region, action="dynamodb:ListTables", resource_id="")
                return
            raise

        for table_name in table_names:
            try:
                response = dynamodb.describe_table(TableName=table_name)
            except ClientError as exc:
                if _is_access_denied(exc):
                    if emitted < self._cfg.max_findings_per_type:
                        emitted += 1
                        yield self._access_error(
                            ctx,
                            region=region,
                            action="dynamodb:DescribeTable",
                            resource_id=table_name,
                        )
                    continue
                raise

            table = (response or {}).get("Table") or {}
            if not isinstance(table, Mapping):
                continue

            table_arn = str(table.get("TableArn") or "")
            table_status = str(table.get("TableStatus") or "").upper()
            billing_mode = str(((table.get("BillingModeSummary") or {}).get("BillingMode")) or "PROVISIONED").upper()
            if table_status != "ACTIVE" or billing_mode != "PROVISIONED":
                continue

            table_provisioned = table.get("ProvisionedThroughput") or {}
            read_capacity = _to_int(table_provisioned.get("ReadCapacityUnits"))
            write_capacity = _to_int(table_provisioned.get("WriteCapacityUnits"))

            utilization = self._fetch_utilization(
                cloudwatch,
                start=start,
                end=now_ts,
                dimensions=[{"Name": "TableName", "Value": table_name}],
            )
            if utilization is None:
                if emitted < self._cfg.max_findings_per_type:
                    emitted += 1
                    yield self._access_error(
                        ctx,
                        region=region,
                        action="cloudwatch:GetMetricData",
                        resource_id=table_name,
                    )
                return

            table_util_pct = self._utilization_pct(
                utilization=utilization,
                read_capacity=read_capacity,
                write_capacity=write_capacity,
            )
            if table_util_pct is not None and table_util_pct < float(self._cfg.underutilized_util_threshold_pct):
                if emitted >= self._cfg.max_findings_per_type:
                    return
                emitted += 1
                yield FindingDraft(
                    check_id="aws.dynamodb.table.provisioned.underutilized",
                    check_name="DynamoDB provisioned table underutilized",
                    category="cost",
                    status="info",
                    severity=Severity(level="low", score=280),
                    title=f"DynamoDB table provisioned throughput appears underutilized: {table_name}",
                    scope=build_scope(
                        ctx,
                        account=self._account,
                        region=region,
                        service="dynamodb",
                        resource_type="table",
                        resource_id=table_name,
                        resource_arn=table_arn,
                    ),
                    message=(
                        f"Observed p95 provisioned-capacity utilization is {table_util_pct:.2f}% over the last "
                        f"{self._cfg.lookback_days} days."
                    ),
                    recommendation=(
                        "Review provisioned read/write capacity or enable Application Auto Scaling if workload "
                        "peaks do not justify the current baseline."
                    ),
                    dimensions={
                        "table_name": table_name,
                        "billing_mode": billing_mode,
                        "read_capacity_units": str(read_capacity),
                        "write_capacity_units": str(write_capacity),
                        "p95_consumed_read_units": f"{utilization['p95_read']:.2f}",
                        "p95_consumed_write_units": f"{utilization['p95_write']:.2f}",
                        "p95_utilization_pct": f"{table_util_pct:.2f}",
                    },
                    issue_key={"signal": "table_underutilized", "table_name": table_name},
                )

            for gsi in table.get("GlobalSecondaryIndexes", []) or []:
                if not isinstance(gsi, Mapping):
                    continue
                index_name = str(gsi.get("IndexName") or "").strip()
                if not index_name:
                    continue
                index_status = str(gsi.get("IndexStatus") or "").upper()
                if index_status != "ACTIVE":
                    continue
                provisioned = gsi.get("ProvisionedThroughput") or {}
                gsi_read_capacity = _to_int(provisioned.get("ReadCapacityUnits"))
                gsi_write_capacity = _to_int(provisioned.get("WriteCapacityUnits"))
                gsi_utilization = self._fetch_utilization(
                    cloudwatch,
                    start=start,
                    end=now_ts,
                    dimensions=[
                        {"Name": "TableName", "Value": table_name},
                        {"Name": "GlobalSecondaryIndexName", "Value": index_name},
                    ],
                )
                if gsi_utilization is None:
                    if emitted < self._cfg.max_findings_per_type:
                        emitted += 1
                        yield self._access_error(
                            ctx,
                            region=region,
                            action="cloudwatch:GetMetricData",
                            resource_id=f"{table_name}/{index_name}",
                        )
                    return

                gsi_util_pct = self._utilization_pct(
                    utilization=gsi_utilization,
                    read_capacity=gsi_read_capacity,
                    write_capacity=gsi_write_capacity,
                )
                if gsi_util_pct is None or gsi_util_pct >= float(self._cfg.underutilized_util_threshold_pct):
                    continue
                if emitted >= self._cfg.max_findings_per_type:
                    return
                emitted += 1
                yield FindingDraft(
                    check_id="aws.dynamodb.gsi.provisioned.underutilized",
                    check_name="DynamoDB provisioned GSI underutilized",
                    category="cost",
                    status="info",
                    severity=Severity(level="low", score=270),
                    title=f"DynamoDB GSI provisioned throughput appears underutilized: {index_name}",
                    scope=build_scope(
                        ctx,
                        account=self._account,
                        region=region,
                        service="dynamodb",
                        resource_type="gsi",
                        resource_id=f"{table_name}/{index_name}",
                        resource_arn=table_arn,
                    ),
                    message=(
                        f"Observed p95 provisioned-capacity utilization for GSI '{index_name}' is "
                        f"{gsi_util_pct:.2f}% over the last {self._cfg.lookback_days} days."
                    ),
                    recommendation=(
                        "Review GSI provisioned read/write capacity or auto scaling if current throughput "
                        "materially exceeds observed demand."
                    ),
                    dimensions={
                        "table_name": table_name,
                        "gsi_name": index_name,
                        "read_capacity_units": str(gsi_read_capacity),
                        "write_capacity_units": str(gsi_write_capacity),
                        "p95_consumed_read_units": f"{gsi_utilization['p95_read']:.2f}",
                        "p95_consumed_write_units": f"{gsi_utilization['p95_write']:.2f}",
                        "p95_utilization_pct": f"{gsi_util_pct:.2f}",
                    },
                    issue_key={
                        "signal": "gsi_underutilized",
                        "table_name": table_name,
                        "gsi_name": index_name,
                    },
                )

    def _fetch_utilization(
        self,
        cloudwatch: Any,
        *,
        start: Any,
        end: Any,
        dimensions: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        try:
            response = cloudwatch.get_metric_data(
                MetricDataQueries=[
                    _metric_query("read", dimensions=dimensions, metric_name="ConsumedReadCapacityUnits"),
                    _metric_query("write", dimensions=dimensions, metric_name="ConsumedWriteCapacityUnits"),
                ],
                StartTime=start,
                EndTime=end,
                ScanBy="TimestampAscending",
                MaxDatapoints=5000,
            )
        except (BotoCoreError, ClientError) as exc:
            if isinstance(exc, ClientError) and _is_access_denied(exc):
                return None
            raise

        metric_rows = (response or {}).get("MetricDataResults", []) or []
        read_values: list[float] = []
        write_values: list[float] = []
        for row in metric_rows:
            if not isinstance(row, Mapping):
                continue
            query_id = str(row.get("Id") or "")
            values = [common.safe_float(value) for value in (row.get("Values", []) or [])]
            if query_id == "read":
                read_values = values
            elif query_id == "write":
                write_values = values

        datapoints = max(len(read_values), len(write_values))
        if datapoints < int(self._cfg.min_datapoints):
            return {}
        return {
            "p95_read": float(_p95(read_values) or 0.0),
            "p95_write": float(_p95(write_values) or 0.0),
        }

    @staticmethod
    def _utilization_pct(
        *,
        utilization: dict[str, Any],
        read_capacity: int,
        write_capacity: int,
    ) -> float | None:
        if not utilization:
            return None
        read_util = 0.0 if read_capacity <= 0 else (float(utilization.get("p95_read") or 0.0) / float(read_capacity)) * 100.0
        write_util = 0.0 if write_capacity <= 0 else (float(utilization.get("p95_write") or 0.0) / float(write_capacity)) * 100.0
        if read_capacity <= 0 and write_capacity <= 0:
            return None
        return max(read_util, write_util)

    def _access_error(
        self,
        ctx: RunContext,
        *,
        region: str,
        action: str,
        resource_id: str,
    ) -> FindingDraft:
        return FindingDraft(
            check_id="aws.dynamodb.access.error",
            check_name="DynamoDB API access error",
            category="governance",
            status="info",
            severity=Severity(level="info", score=0),
            title="Unable to collect full DynamoDB inventory due to IAM restrictions",
            scope=build_scope(
                ctx,
                account=self._account,
                region=region,
                service="dynamodb",
                resource_type="table",
                resource_id=resource_id or self._account.account_id,
            ),
            message=f"Access denied calling {action} in region '{region}'.",
            recommendation=(
                "Grant least-privilege read permissions for DynamoDB and CloudWatch metrics so the platform can "
                "evaluate provisioned-capacity utilization."
            ),
            issue_key={
                "signal": "access_error",
                "service": "dynamodb",
                "action": action,
                "region": region,
                "resource_id": resource_id,
            },
        )


@register_checker("checks.aws.dynamodb_tables:DynamoDbTablesChecker")
def _factory(ctx: RunContext, bootstrap: Bootstrap) -> DynamoDbTablesChecker:
    account_id = str(bootstrap.get("aws_account_id") or "")
    if not account_id:
        raise RuntimeError("aws_account_id missing from bootstrap (required for DynamoDbTablesChecker)")
    billing_account_id = str(bootstrap.get("aws_billing_account_id") or account_id)
    return DynamoDbTablesChecker(
        account=AwsAccountContext(account_id=account_id, billing_account_id=billing_account_id),
    )
