"""Unit tests for the DynamoDB tables checker."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, cast

from botocore.exceptions import ClientError

from checks.aws.dynamodb_tables import DynamoDbTablesChecker
from contracts.finops_checker_pattern import RunContext


class FakePaginator:
    def __init__(self, pages: List[Mapping[str, Any]]) -> None:
        self._pages = pages

    def paginate(self, **_kwargs: Any) -> Iterable[Mapping[str, Any]]:
        yield from self._pages


class FakeDynamoDb:
    """Minimal DynamoDB fake."""

    def __init__(
        self,
        *,
        region: str,
        table_names: List[str],
        tables_by_name: Dict[str, Mapping[str, Any]],
        raise_on: Optional[str] = None,
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self._table_names = table_names
        self._tables_by_name = tables_by_name
        self._raise_on = raise_on

    def get_paginator(self, op_name: str) -> FakePaginator:
        if self._raise_on == op_name:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                op_name,
            )
        if op_name == "list_tables":
            return FakePaginator([{"TableNames": list(self._table_names)}])
        raise KeyError(op_name)

    def describe_table(self, *, TableName: str) -> Mapping[str, Any]:
        if self._raise_on == "describe_table":
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "describe_table",
            )
        return {"Table": dict(self._tables_by_name.get(TableName, {}))}


class FakeCloudWatch:
    """Minimal CloudWatch fake for DynamoDB metrics."""

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
            table_name = ""
            gsi_name = ""
            for dimension in dimensions:
                if not isinstance(dimension, Mapping):
                    continue
                if dimension.get("Name") == "TableName":
                    table_name = str(dimension.get("Value") or "")
                if dimension.get("Name") == "GlobalSecondaryIndexName":
                    gsi_name = str(dimension.get("Value") or "")
            key = (table_name, gsi_name, metric_name)
            results.append({"Id": query_id, "Values": list(self._metrics_by_key.get(key, []))})
        return {"MetricDataResults": results}


def _mk_ctx(*, dynamodb: Any, cloudwatch: Any, region: str = "eu-west-1") -> RunContext:
    return cast(
        RunContext,
        SimpleNamespace(
            cloud="aws",
            services=SimpleNamespace(region=region, dynamodb=dynamodb, cloudwatch=cloudwatch),
        ),
    )


def _checker() -> DynamoDbTablesChecker:
    import checks.aws.dynamodb_tables as mod

    return DynamoDbTablesChecker(
        account=mod.AwsAccountContext(account_id="111111111111", billing_account_id="111111111111")
    )


def test_underutilized_provisioned_table_emits() -> None:
    table_arn = "arn:aws:dynamodb:eu-west-1:111111111111:table/orders"
    dynamodb = FakeDynamoDb(
        region="eu-west-1",
        table_names=["orders"],
        tables_by_name={
            "orders": {
                "TableName": "orders",
                "TableArn": table_arn,
                "TableStatus": "ACTIVE",
                "BillingModeSummary": {"BillingMode": "PROVISIONED"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 100, "WriteCapacityUnits": 50},
            }
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("orders", "", "ConsumedReadCapacityUnits"): [4, 6, 8, 10, 12, 9, 7],
            ("orders", "", "ConsumedWriteCapacityUnits"): [1, 2, 3, 2, 4, 2, 1],
        }
    )

    findings = list(_checker().run(_mk_ctx(dynamodb=dynamodb, cloudwatch=cloudwatch)))
    assert len(findings) == 1
    assert findings[0].check_id == "aws.dynamodb.table.provisioned.underutilized"
    assert findings[0].scope.resource_id == "orders"
    assert findings[0].dimensions["optimization_focus"] == "downsize_or_auto_scale_capacity"


def test_underutilized_provisioned_gsi_emits() -> None:
    table_arn = "arn:aws:dynamodb:eu-west-1:111111111111:table/orders"
    dynamodb = FakeDynamoDb(
        region="eu-west-1",
        table_names=["orders"],
        tables_by_name={
            "orders": {
                "TableName": "orders",
                "TableArn": table_arn,
                "TableStatus": "ACTIVE",
                "BillingModeSummary": {"BillingMode": "PROVISIONED"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 20, "WriteCapacityUnits": 20},
                "GlobalSecondaryIndexes": [
                    {
                        "IndexName": "gsi_customer",
                        "IndexStatus": "ACTIVE",
                        "ProvisionedThroughput": {"ReadCapacityUnits": 80, "WriteCapacityUnits": 40},
                    }
                ],
            }
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("orders", "", "ConsumedReadCapacityUnits"): [30, 28, 27, 26, 25, 29, 31],
            ("orders", "", "ConsumedWriteCapacityUnits"): [10, 12, 11, 9, 10, 8, 11],
            ("orders", "gsi_customer", "ConsumedReadCapacityUnits"): [3, 4, 5, 2, 4, 3, 5],
            ("orders", "gsi_customer", "ConsumedWriteCapacityUnits"): [1, 2, 1, 1, 2, 1, 1],
        }
    )

    findings = list(_checker().run(_mk_ctx(dynamodb=dynamodb, cloudwatch=cloudwatch)))
    check_ids = {finding.check_id for finding in findings}
    assert "aws.dynamodb.gsi.provisioned.underutilized" in check_ids
    gsi_hit = next(finding for finding in findings if finding.check_id == "aws.dynamodb.gsi.provisioned.underutilized")
    assert gsi_hit.dimensions["optimization_focus"] == "downsize_or_auto_scale_gsi"


def test_very_low_util_table_recommends_on_demand_and_standard_ia_review() -> None:
    table_arn = "arn:aws:dynamodb:eu-west-1:111111111111:table/archive-orders"
    dynamodb = FakeDynamoDb(
        region="eu-west-1",
        table_names=["archive-orders"],
        tables_by_name={
            "archive-orders": {
                "TableName": "archive-orders",
                "TableArn": table_arn,
                "TableStatus": "ACTIVE",
                "BillingModeSummary": {"BillingMode": "PROVISIONED"},
                "TableClassSummary": {"TableClass": "STANDARD"},
                "TableSizeBytes": 150 * 1024 * 1024 * 1024,
                "ItemCount": 5000000,
                "ProvisionedThroughput": {"ReadCapacityUnits": 100, "WriteCapacityUnits": 100},
            }
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("archive-orders", "", "ConsumedReadCapacityUnits"): [1, 1, 2, 1, 1, 2, 1],
            ("archive-orders", "", "ConsumedWriteCapacityUnits"): [0, 1, 1, 1, 0, 1, 1],
        }
    )

    findings = list(_checker().run(_mk_ctx(dynamodb=dynamodb, cloudwatch=cloudwatch)))
    check_ids = {finding.check_id for finding in findings}
    assert "aws.dynamodb.table.provisioned.underutilized" in check_ids
    assert "aws.dynamodb.table.class.standard_ia.review" in check_ids
    table_hit = next(finding for finding in findings if finding.check_id == "aws.dynamodb.table.provisioned.underutilized")
    assert table_hit.dimensions["optimization_focus"] == "move_to_on_demand_review"
    class_hit = next(finding for finding in findings if finding.check_id == "aws.dynamodb.table.class.standard_ia.review")
    assert class_hit.dimensions["optimization_focus"] == "standard_ia_review"


def test_low_traffic_gsi_emits_possibly_unused_instead_of_underutilized() -> None:
    table_arn = "arn:aws:dynamodb:eu-west-1:111111111111:table/orders"
    dynamodb = FakeDynamoDb(
        region="eu-west-1",
        table_names=["orders"],
        tables_by_name={
            "orders": {
                "TableName": "orders",
                "TableArn": table_arn,
                "TableStatus": "ACTIVE",
                "BillingModeSummary": {"BillingMode": "PROVISIONED"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 20, "WriteCapacityUnits": 20},
                "GlobalSecondaryIndexes": [
                    {
                        "IndexName": "gsi_unused",
                        "IndexStatus": "ACTIVE",
                        "ProvisionedThroughput": {"ReadCapacityUnits": 50, "WriteCapacityUnits": 50},
                    }
                ],
            }
        },
    )
    cloudwatch = FakeCloudWatch(
        metrics_by_key={
            ("orders", "", "ConsumedReadCapacityUnits"): [10, 11, 12, 11, 10, 12, 11],
            ("orders", "", "ConsumedWriteCapacityUnits"): [8, 9, 8, 9, 8, 9, 8],
            ("orders", "gsi_unused", "ConsumedReadCapacityUnits"): [0, 0, 1, 0, 0, 1, 0],
            ("orders", "gsi_unused", "ConsumedWriteCapacityUnits"): [0, 0, 0, 1, 0, 0, 0],
        }
    )

    findings = list(_checker().run(_mk_ctx(dynamodb=dynamodb, cloudwatch=cloudwatch)))
    check_ids = {finding.check_id for finding in findings}
    assert "aws.dynamodb.gsi.possibly.unused" in check_ids
    assert "aws.dynamodb.gsi.provisioned.underutilized" not in check_ids
    hit = next(finding for finding in findings if finding.check_id == "aws.dynamodb.gsi.possibly.unused")
    assert hit.dimensions["optimization_focus"] == "delete_or_consolidate_gsi"


def test_access_denied_emits_info_finding() -> None:
    dynamodb = FakeDynamoDb(
        region="eu-west-1",
        table_names=[],
        tables_by_name={},
        raise_on="list_tables",
    )
    cloudwatch = FakeCloudWatch(metrics_by_key={})

    findings = list(_checker().run(_mk_ctx(dynamodb=dynamodb, cloudwatch=cloudwatch)))
    assert len(findings) == 1
    assert findings[0].check_id == "aws.dynamodb.access.error"
    assert findings[0].status == "info"
