"""Rule-level tests for correlating RDS backup-governance context gaps."""

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


def _draft_rds_signal(
    *,
    check_id: str,
    account_id: str,
    region: str,
    db_instance_identifier: str,
    db_cluster_identifier: str = "aurora-prod-cluster",
    db_subnet_group: str = "rds-subnet-group-a",
    monthly_cost: float = 45.0,
) -> FindingDraft:
    """Build one RDS-side signal draft for correlation tests."""

    return FindingDraft(
        check_id=check_id,
        check_name=check_id,
        category="cost",
        sub_category="database",
        status="fail",
        severity=Severity(level="medium", score=720),
        title=f"RDS signal: {check_id} ({db_instance_identifier})",
        message="test fixture",
        recommendation="",
        scope=Scope(
            cloud="aws",
            account_id=account_id,
            region=region,
            service="AmazonRDS",
            resource_type="db_instance",
            resource_id=db_instance_identifier,
            resource_arn=(
                f"arn:aws:rds:{region}:{account_id}:db:{db_instance_identifier}"
            ),
        ),
        estimated_monthly_cost=monthly_cost,
        estimated_monthly_savings=monthly_cost,
        estimate_confidence=45,
        dimensions={
            "db_cluster_identifier": db_cluster_identifier,
            "db_subnet_group": db_subnet_group,
        },
        issue_key={
            "check_id": check_id,
            "account_id": account_id,
            "region": region,
            "db_instance_identifier": db_instance_identifier,
        },
    )


def _draft_backup_signal(
    *,
    check_id: str,
    account_id: str,
    region: str,
    resource_type: str,
    resource_id: str,
    monthly_cost: float = 0.0,
) -> FindingDraft:
    """Build one AWS Backup-side signal draft for correlation tests."""

    return FindingDraft(
        check_id=check_id,
        check_name=check_id,
        category="governance",
        sub_category="backup",
        status="fail",
        severity=Severity(level="medium", score=680),
        title=f"Backup signal: {check_id} ({resource_id})",
        message="test fixture",
        recommendation="",
        scope=Scope(
            cloud="aws",
            account_id=account_id,
            region=region,
            service="AWSBackup",
            resource_type=resource_type,
            resource_id=resource_id,
            resource_arn=(
                f"arn:aws:backup:{region}:{account_id}:{resource_type}/{resource_id}"
            ),
        ),
        estimated_monthly_cost=monthly_cost if monthly_cost > 0 else None,
        estimated_monthly_savings=monthly_cost if monthly_cost > 0 else None,
        estimate_confidence=35,
        issue_key={
            "check_id": check_id,
            "account_id": account_id,
            "region": region,
            "resource_id": resource_id,
        },
    )


def test_rule_emits_when_rds_and_multiple_backup_gaps_coexist() -> None:
    """Emit the correlation when an RDS resource shares an environment with 2+ backup gaps."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("rds_backup_gap_emits")

    drafts = [
        _draft_rds_signal(
            check_id="aws.rds.storage.overprovisioned",
            account_id="123456789012",
            region="eu-west-1",
            db_instance_identifier="db-prod-1",
        ),
        _draft_backup_signal(
            check_id="aws.backup.vaults.no.lifecycle",
            account_id="123456789012",
            region="eu-west-1",
            resource_type="backup_vault",
            resource_id="vault-a",
        ),
        _draft_backup_signal(
            check_id="aws.backup.plans.no.selections",
            account_id="123456789012",
            region="eu-west-1",
            resource_type="backup_plan",
            resource_id="plan-a",
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

    corr_rows = [r for r in rows if r.get("check_id") == "aws.rds.correlation.backup.gap.context"]
    assert len(corr_rows) == 1
    assert corr_rows[0]["scope"]["resource_id"] == "db-prod-1"


def test_rule_does_not_emit_with_only_one_backup_signal() -> None:
    """Skip the correlation when the environment has only one backup signal."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("rds_backup_gap_no_emit")

    drafts = [
        _draft_rds_signal(
            check_id="aws.rds.read.replica.unused",
            account_id="123456789012",
            region="eu-west-1",
            db_instance_identifier="db-replica-1",
        ),
        _draft_backup_signal(
            check_id="aws.backup.vaults.no.lifecycle",
            account_id="123456789012",
            region="eu-west-1",
            resource_type="backup_vault",
            resource_id="vault-b",
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
    assert len([r for r in rows if r.get("check_id") == "aws.rds.correlation.backup.gap.context"]) == 0


def test_rule_is_deterministic_for_same_input() -> None:
    """Produce identical correlated output for the same raw RDS and backup inputs."""

    corr = make_ctx()
    raw_dir, _unused_out_dir = _case_dirs("rds_backup_gap_deterministic")
    out_dir1 = raw_dir.parent / "corr1"
    out_dir2 = raw_dir.parent / "corr2"

    drafts = [
        _draft_rds_signal(
            check_id="aws.rds.instances.stopped.storage",
            account_id="123456789012",
            region="eu-west-1",
            db_instance_identifier="db-deterministic",
        ),
        _draft_backup_signal(
            check_id="aws.backup.rules.no.lifecycle",
            account_id="123456789012",
            region="eu-west-1",
            resource_type="backup_rule",
            resource_id="rule-1",
        ),
        _draft_backup_signal(
            check_id="aws.backup.recovery.points.stale",
            account_id="123456789012",
            region="eu-west-1",
            resource_type="recovery_point",
            resource_id="rp-1",
            monthly_cost=12.0,
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
