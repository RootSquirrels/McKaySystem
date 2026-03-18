# AWS DynamoDB Tables checker

Status: Canonical  
Last reviewed: 2026-03-17

**Source code:** `checks/aws/dynamodb_tables.py`

## Purpose

Detect DynamoDB tables and global secondary indexes (GSIs) in provisioned mode
that appear materially underutilized relative to their configured throughput,
plus large low-traffic tables that may fit Standard-IA better.

## Checker identity

- `checker_id`: `aws.dynamodb.tables.audit`
- `spec`: `checks.aws.dynamodb_tables:DynamoDbTablesChecker`

## Check IDs emitted

- `aws.dynamodb.table.provisioned.underutilized`
- `aws.dynamodb.table.class.standard_ia.review`
- `aws.dynamodb.gsi.possibly.unused`
- `aws.dynamodb.gsi.provisioned.underutilized`
- `aws.dynamodb.access.error`

## Key signals

- Active DynamoDB tables in `PROVISIONED` billing mode with low observed p95
  consumed read/write capacity versus current provisioned throughput, with
  clearer action guidance such as on-demand review vs capacity downsize.
- Large `STANDARD`-class provisioned tables with very low observed traffic that
  may fit `STANDARD_INFREQUENT_ACCESS` better.
- Active GSIs in `PROVISIONED` mode with either low observed utilization or a
  stronger "possibly unused" pattern when observed p95 read/write demand is
  near zero.

## Configuration and defaults

Configured via `DynamoDbTablesConfig`.
Defaults are sourced from `checks/aws/defaults.py`, including:

- `DYNAMODB_LOOKBACK_DAYS`
- `DYNAMODB_MIN_DATAPOINTS`
- `DYNAMODB_UNDERUTILIZED_UTIL_THRESHOLD_PCT`
- `DYNAMODB_MOVE_TO_ON_DEMAND_UTIL_THRESHOLD_PCT`
- `DYNAMODB_UNUSED_GSI_MAX_P95_CAPACITY_UNITS`
- `DYNAMODB_STANDARD_IA_MAX_UTIL_THRESHOLD_PCT`
- `DYNAMODB_STANDARD_IA_MIN_TABLE_SIZE_GIB`
- `DYNAMODB_MAX_FINDINGS_PER_TYPE`

## IAM permissions

Typical read-only permissions:

- `dynamodb:ListTables`
- `dynamodb:DescribeTable`
- `cloudwatch:GetMetricData`

## Determinism and limitations

- Findings are deterministic for equivalent DynamoDB inventory and CloudWatch
  metric input.
- The checker currently focuses on provisioned tables and GSIs only.
- Standard-IA recommendations are emitted as review findings, not deterministic
  savings ownership, because storage and access economics vary by workload.
- It does not yet estimate savings or analyze on-demand tables, backups, or GSI
  query dependencies directly.
- Access-denied scenarios are emitted as informational findings.

## Related tests

- `tests/test_dynamodb_tables.py`
