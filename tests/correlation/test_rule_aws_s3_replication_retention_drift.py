"""Rule-level tests for correlating S3 replication and retention drift."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from contracts.finops_checker_pattern import FindingDraft, Scope, Severity

from tests.correlation._harness import (
    make_ctx,
    run_correlation_and_read_rows,
    signature,
    write_input_parquet_single_file,
)


def _case_dirs(case_name: str) -> tuple[Path, Path]:
    root = Path(".tmp_test_correlation") / f"{case_name}_{uuid4().hex}"
    raw_dir = root / "finops_findings"
    out_dir = root / "finops_findings_correlated"
    return raw_dir, out_dir


def _draft_bucket_signal(
    *,
    check_id: str,
    account_id: str,
    region: str,
    bucket_name: str,
    score: int = 720,
    dimensions: dict[str, str] | None = None,
) -> FindingDraft:
    """Build one S3 bucket signal draft for correlation tests."""

    return FindingDraft(
        check_id=check_id,
        check_name=check_id,
        category="governance",
        status="fail",
        severity=Severity(level="high" if score >= 800 else "medium", score=score),
        title=f"S3 signal: {check_id} ({bucket_name})",
        scope=Scope(
            cloud="aws",
            account_id=account_id,
            region=region,
            service="s3",
            resource_type="bucket",
            resource_id=bucket_name,
            resource_arn=f"arn:aws:s3:::{bucket_name}",
        ),
        message="test fixture",
        recommendation="",
        estimate_confidence=35,
        dimensions=dimensions or {},
        issue_key={"check_id": check_id, "account_id": account_id, "region": region, "bucket": bucket_name},
    )


def test_rule_emits_when_replication_review_stacks_with_retention_gap() -> None:
    """Emit the correlation when replication review and retention drift coexist."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("s3_rrd_emit")

    bucket_name = "replication-drift-a"
    drafts = [
        _draft_bucket_signal(
            check_id="aws.s3.cost.replication.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name=bucket_name,
            score=820,
            dimensions={
                "replication_pattern": "cold_data_replication_review",
                "recommendation_focus": "destination_storage_class",
            },
        ),
        _draft_bucket_signal(
            check_id="aws.s3.cost.lifecycle.transition.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name=bucket_name,
            dimensions={"recommended_transition_target": "Glacier Instant Retrieval"},
        ),
    ]

    write_input_parquet_single_file(base_dir=raw_dir, ctx=corr.ctx, drafts=drafts)

    stats, rows = run_correlation_and_read_rows(
        corr_run=corr,
        raw_dir=raw_dir,
        out_dir=out_dir,
        threads=2,
        finding_id_mode="stable",
    )

    assert stats["enabled"] is True
    assert stats["errors"] == 0

    corr_rows = [r for r in rows if r.get("check_id") == "aws.s3.correlation.replication.retention.drift"]
    assert len(corr_rows) == 1
    assert corr_rows[0]["scope"]["resource_id"] == bucket_name


def test_rule_does_not_emit_without_replication_review() -> None:
    """Skip the correlation when the bucket lacks replication complexity."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("s3_rrd_noemit")

    drafts = [
        _draft_bucket_signal(
            check_id="aws.s3.governance.lifecycle.missing",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-b",
        ),
        _draft_bucket_signal(
            check_id="aws.s3.cost.intelligent_tiering.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-b",
        ),
    ]

    write_input_parquet_single_file(base_dir=raw_dir, ctx=corr.ctx, drafts=drafts)

    stats, rows = run_correlation_and_read_rows(
        corr_run=corr,
        raw_dir=raw_dir,
        out_dir=out_dir,
        threads=2,
        finding_id_mode="stable",
    )

    assert stats["enabled"] is True
    assert stats["errors"] == 0
    assert len([r for r in rows if r.get("check_id") == "aws.s3.correlation.replication.retention.drift"]) == 0


def test_rule_is_deterministic_for_same_input() -> None:
    """Produce identical correlated output for the same raw S3 input."""

    corr = make_ctx()
    raw_dir, _unused_out_dir = _case_dirs("s3_rrd_det")
    out_dir1 = raw_dir.parent / "corr1"
    out_dir2 = raw_dir.parent / "corr2"

    drafts = [
        _draft_bucket_signal(
            check_id="aws.s3.cost.replication.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-deterministic",
            dimensions={"replication_pattern": "general_replication_review"},
        ),
        _draft_bucket_signal(
            check_id="aws.s3.cost.intelligent_tiering.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-deterministic",
        ),
    ]

    write_input_parquet_single_file(base_dir=raw_dir, ctx=corr.ctx, drafts=drafts)

    _, rows1 = run_correlation_and_read_rows(
        corr_run=corr,
        raw_dir=raw_dir,
        out_dir=out_dir1,
        threads=2,
        finding_id_mode="stable",
    )
    _, rows2 = run_correlation_and_read_rows(
        corr_run=corr,
        raw_dir=raw_dir,
        out_dir=out_dir2,
        threads=2,
        finding_id_mode="stable",
    )

    assert signature(rows1) == signature(rows2)
