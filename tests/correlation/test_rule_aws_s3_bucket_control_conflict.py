"""Rule-level tests for correlating stacked S3 bucket control gaps."""

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
    score: int = 700,
    dimensions: dict[str, str] | None = None,
) -> FindingDraft:
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


def test_rule_emits_when_bucket_has_two_or_more_control_gaps() -> None:
    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("s3_conflict_emits")

    bucket_name = "bucket-a"
    drafts = [
        _draft_bucket_signal(
            check_id="aws.s3.governance.public.access.block.missing",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name=bucket_name,
        ),
        _draft_bucket_signal(
            check_id="aws.s3.governance.lifecycle.missing",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name=bucket_name,
        ),
        _draft_bucket_signal(
            check_id="aws.s3.cost.replication.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name=bucket_name,
            score=820,
            dimensions={"replication_pattern": "cold_data_replication_review", "recommendation_focus": "scope_review"},
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

    corr_rows = [r for r in rows if r.get("check_id") == "aws.s3.correlation.bucket.control.conflict"]
    assert len(corr_rows) == 1
    assert corr_rows[0]["scope"]["resource_id"] == bucket_name


def test_rule_does_not_emit_with_only_one_bucket_signal() -> None:
    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("s3_conflict_no_emit")

    drafts = [
        _draft_bucket_signal(
            check_id="aws.s3.governance.public.access.block.missing",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-b",
        )
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
    assert len([r for r in rows if r.get("check_id") == "aws.s3.correlation.bucket.control.conflict"]) == 0


def test_rule_is_deterministic_for_same_input() -> None:
    corr = make_ctx()
    raw_dir, _unused_out_dir = _case_dirs("s3_conflict_deterministic")
    out_dir1 = raw_dir.parent / "corr1"
    out_dir2 = raw_dir.parent / "corr2"

    drafts = [
        _draft_bucket_signal(
            check_id="aws.s3.governance.public.access.block.missing",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-deterministic",
        ),
        _draft_bucket_signal(
            check_id="aws.s3.cost.replication.review",
            account_id="123456789012",
            region="eu-west-1",
            bucket_name="bucket-deterministic",
            dimensions={"replication_pattern": "general_replication_review"},
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
