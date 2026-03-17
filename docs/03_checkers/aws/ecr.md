# AWS ECR Images checker

Status: Canonical  
Last reviewed: 2026-03-17

**Source code:** `checks/aws/ecr_images.py`

## Purpose

Detect stale Amazon ECR images that may no longer be needed for active
deployments, rollback, or compliance retention.

## Checker identity

- `checker_id`: `aws.ecr.images.audit`
- `spec`: `checks.aws.ecr_images:EcrImagesChecker`

## Check IDs emitted

- `aws.ecr.images.stale`
- `aws.ecr.access.error`

## Key signals

- Images pushed longer ago than the configured stale threshold.
- Images with no recorded pull activity, or no pull within the same threshold.

## Configuration and defaults

Configured via `EcrImagesConfig`.
Defaults are sourced from `checks/aws/defaults.py`, including:

- `ECR_STALE_IMAGE_DAYS`
- `ECR_MAX_FINDINGS_PER_TYPE`

## IAM permissions

Typical read-only permissions:

- `ecr:DescribeRepositories`
- `ecr:DescribeImages`

## Determinism and limitations

- Findings are deterministic for equivalent ECR inventory input.
- Staleness is heuristic and based only on repository/image metadata.
- The checker does not prove that an image is safe to delete; it flags candidates
  for review.
- Access-denied scenarios are emitted as informational findings.

## Related tests

- `tests/test_ecr_images.py`
