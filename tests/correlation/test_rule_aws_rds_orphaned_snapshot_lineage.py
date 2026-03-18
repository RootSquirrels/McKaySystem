"""Rule-level tests for correlating orphaned RDS snapshot lineage."""

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


def _draft_orphaned_snapshot(
    *,
    account_id: str,
    region: str,
    snapshot_id: str,
    source_identifier: str,
    source_kind: str = "db_instance",
    resource_type: str = "rds_db_snapshot",
    monthly_cost: float = 8.0,
) -> FindingDraft:
    """Build one orphaned RDS snapshot draft."""

    arn_kind = "cluster-snapshot" if resource_type == "rds_cluster_snapshot" else "snapshot"
    return FindingDraft(
        check_id="aws.rds.snapshots.orphaned",
        check_name="Orphaned RDS snapshot",
        category="waste",
        sub_category="backup",
        status="fail",
        severity=Severity(level="medium", score=700),
        title=f"Orphaned snapshot {snapshot_id}",
        message="test fixture",
        recommendation="",
        scope=Scope(
            cloud="aws",
            account_id=account_id,
            region=region,
            service="AmazonRDS",
            resource_type=resource_type,
            resource_id=snapshot_id,
            resource_arn=f"arn:aws:rds:{region}:{account_id}:{arn_kind}:{snapshot_id}",
        ),
        estimated_monthly_cost=monthly_cost,
        estimated_monthly_savings=monthly_cost,
        estimate_confidence=50,
        dimensions={
            "snapshot_id": snapshot_id,
            "source_identifier": source_identifier,
            "source_kind": source_kind,
            "snapshot_kind": "cluster" if resource_type == "rds_cluster_snapshot" else "instance",
        },
        issue_key={
            "rule": "orphaned",
            "snapshot_id": snapshot_id,
            "resource_type": resource_type,
        },
    )


def test_rule_emits_when_multiple_orphaned_snapshots_share_same_source() -> None:
    """Emit the correlation for a repeated orphaned snapshot lineage."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("rds_orphaned_lineage_emits")

    drafts = [
        _draft_orphaned_snapshot(
            account_id="123456789012",
            region="eu-west-1",
            snapshot_id="snap-a",
            source_identifier="db-retired-1",
        ),
        _draft_orphaned_snapshot(
            account_id="123456789012",
            region="eu-west-1",
            snapshot_id="snap-b",
            source_identifier="db-retired-1",
            monthly_cost=12.0,
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

    corr_rows = [r for r in rows if r.get("check_id") == "aws.rds.correlation.snapshots.orphaned.lineage"]
    assert len(corr_rows) == 1
    assert corr_rows[0]["scope"]["resource_id"] == "db-retired-1"


def test_rule_does_not_emit_for_single_orphaned_snapshot() -> None:
    """Skip the correlation when the orphaned lineage has only one snapshot."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("rds_orphaned_lineage_no_emit")

    drafts = [
        _draft_orphaned_snapshot(
            account_id="123456789012",
            region="eu-west-1",
            snapshot_id="snap-single",
            source_identifier="db-retired-2",
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
    assert len([r for r in rows if r.get("check_id") == "aws.rds.correlation.snapshots.orphaned.lineage"]) == 0


def test_rule_is_deterministic_for_same_input() -> None:
    """Produce identical correlated output for the same raw orphaned lineage input."""

    corr = make_ctx()
    raw_dir, _unused_out_dir = _case_dirs("rds_orphaned_lineage_deterministic")
    out_dir1 = raw_dir.parent / "corr1"
    out_dir2 = raw_dir.parent / "corr2"

    drafts = [
        _draft_orphaned_snapshot(
            account_id="123456789012",
            region="eu-west-1",
            snapshot_id="snap-d1",
            source_identifier="cluster-retired-1",
            source_kind="db_cluster",
            resource_type="rds_cluster_snapshot",
        ),
        _draft_orphaned_snapshot(
            account_id="123456789012",
            region="eu-west-1",
            snapshot_id="snap-d2",
            source_identifier="cluster-retired-1",
            source_kind="db_cluster",
            resource_type="rds_cluster_snapshot",
            monthly_cost=14.0,
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
