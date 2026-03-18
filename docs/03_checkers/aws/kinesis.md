# AWS Kinesis Data Streams Checker

**Source code:** `checks/aws/kinesis_streams.py`

## Purpose

Detect decision-grade `v1` Kinesis Data Streams optimization opportunities
without adding broad or noisy heuristics.

This checker focuses on:

- clearly overprovisioned provisioned shard count
- low-value extended retention
- potentially unused enhanced fan-out consumers

It only evaluates `Kinesis Data Streams`, not Firehose.

## Checker identity

- `checker_id`: `aws.kinesis.streams.audit`
- `spec`: `checks.aws.kinesis_streams:KinesisStreamsChecker`

## Findings emitted

- `aws.kinesis.stream.provisioned.overprovisioned`
- `aws.kinesis.stream.retention.extended.review`
- `aws.kinesis.stream.enhanced_fanout.unused.review`
- `aws.kinesis.access.error`

## Evidence model

The checker uses CloudWatch `GetMetricData` with daily sums over the configured
lookback window and derives:

- `p95_incoming_bytes`
- `p95_outgoing_bytes`
- `p95_incoming_records`
- `p95_outgoing_records`

For provisioned streams, shard-fit is conservative and based on ingest-side
capacity only:

- `1 MiB/s` write throughput per shard
- `1000 records/s` write rate per shard

This avoids overstating resizing opportunities from consumer-side fan-out.

## Savings and confidence

`aws.kinesis.stream.provisioned.overprovisioned` is the only direct savings
owner in `v1`.

Its estimate:

- uses fallback shard-hour pricing
- calculates reducible shard count conservatively from p95 daily ingress
- should be treated as directional until pricing/CUR enrichment is deeper

Retention and enhanced fan-out findings are review signals:

- they improve actionability
- they should not be treated as exact savings claims

## IAM permissions

- `kinesis:ListStreams`
- `kinesis:DescribeStreamSummary`
- `kinesis:ListStreamConsumers`
- `cloudwatch:GetMetricData`

If any of these are denied, the checker emits `aws.kinesis.access.error`.

## Tests

- `tests/test_kinesis_streams.py`
