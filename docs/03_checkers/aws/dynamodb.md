# AWS DynamoDB Tables checker

Status: Canonical  
Last reviewed: 2026-03-17

**Source code:** `checks/aws/dynamodb_tables.py`

## Purpose

Detect DynamoDB tables and global secondary indexes (GSIs) in provisioned mode
that appear materially underutilized relative to their configured throughput.

## Checker identity

- `checker_id`: `aws.dynamodb.tables.audit`
- `spec`: `checks.aws.dynamodb_tables:DynamoDbTablesChecker`

## Check IDs emitted

- `aws.dynamodb.table.provisioned.underutilized`
- `aws.dynamodb.gsi.provisioned.underutilized`
- `aws.dynamodb.access.error`

## Key signals

- Active DynamoDB tables in `PROVISIONED` billing mode with low observed p95
  consumed read/write capacity versus current provisioned throughput.
- Active GSIs in `PROVISIONED` mode with the same underutilization heuristic.

## Configuration and defaults

Configured via `DynamoDbTablesConfig`.
Defaults are sourced from `checks/aws/defaults.py`, including:

- `DYNAMODB_LOOKBACK_DAYS`
- `DYNAMODB_MIN_DATAPOINTS`
- `DYNAMODB_UNDERUTILIZED_UTIL_THRESHOLD_PCT`
- `DYNAMODB_MAX_FINDINGS_PER_TYPE`

## IAM permissions

Typical read-only permissions:

- `dynamodb:ListTables`
- `dynamodb:DescribeTable`
- `cloudwatch:GetMetricData`

## Determinism and limitations

- Findings are deterministic for equivalent DynamoDB inventory and CloudWatch
  metric input.
- The checker currently evaluates provisioned-capacity underutilization only.
- It does not yet estimate savings or analyze on-demand tables, old backups, or
  unused GSIs without capacity metrics.
- Access-denied scenarios are emitted as informational findings.

## Related tests

- `tests/test_dynamodb_tables.py`
