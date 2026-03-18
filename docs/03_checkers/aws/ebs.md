# AWS EBS checker

Status: Canonical  
Last reviewed: 2026-02-15

**Source code:** `checks/aws/ebs_storage.py`

## Purpose

Detect EBS cost and governance inefficiencies across volumes and snapshots.

## Checker identity

- `checker_id`: `aws.ec2.ebs.storage`
- `spec`: `checks.aws.ebs_storage:EBSStorageChecker`

## Check IDs emitted

- `aws.ec2.ebs.unattached.volume`
- `aws.ec2.ebs.gp2.to.gp3`
- `aws.ec2.ebs.gp2.to.gp3.review`
- `aws.ec2.ebs.old.snapshot`
- `aws.ec2.ebs.volume.unencrypted`
- `aws.ec2.ebs.snapshot.unencrypted`
- `aws.ec2.ebs.access.error`

## Key signals

- Unattached volumes older than threshold.
- gp2 volumes eligible for gp3 migration savings when current gp2 baseline performance fits within gp3 included baseline IOPS.
- Large gp2 volumes that may still be gp3 candidates, but need performance review before deterministic savings are claimed.
- Old snapshots not referenced by AMIs.
- Unencrypted volumes/snapshots.
- Informational access-error handling for missing read permissions.

## Configuration and defaults

Configured via `EBSStorageConfig`.
Defaults are sourced from `checks/aws/defaults.py`, including:
- unattached/old snapshot age thresholds
- suppression tag keys/values/prefixes
- max findings safety cap

## IAM permissions

Typical read-only permissions:
- `ec2:DescribeVolumes`
- `ec2:DescribeSnapshots`
- `ec2:DescribeImages`
- `ec2:DescribeInstances`

Optional for improved savings-confidence:
- `pricing:GetProducts` (via pricing service)

## Determinism and limitations

- Suppression tags intentionally reduce false positives for retained backups.
- gp2 to gp3 direct savings are only emitted for volumes whose implied gp2 baseline IOPS do not exceed gp3 included baseline IOPS.
- Large gp2 volumes are emitted as review findings without savings to avoid overstating savings when paid gp3 IOPS or throughput add-ons may be required.
- Savings/cost values are best-effort and storage-price dependent.
- Access-denied scenarios are surfaced as informational findings.

## Related tests

- `tests/test_ebs_storage.py`
