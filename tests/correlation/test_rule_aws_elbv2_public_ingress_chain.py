"""Rule-level tests for correlating public ELB ingress-chain issues."""

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


def _draft_lb_signal(
    *,
    check_id: str,
    account_id: str,
    region: str,
    lb_name: str,
    scheme: str = "internet-facing",
    lb_type: str = "application",
    vpc_id: str = "vpc-12345",
    subnet_ids: str = "subnet-a,subnet-b",
    target_group_arns: str = "arn:aws:elasticloadbalancing:eu-west-1:123456789012:targetgroup/tg-1/abc",
    score: int = 780,
    monthly_cost: float = 18.0,
) -> FindingDraft:
    """Build one ELB signal draft for correlation tests."""

    return FindingDraft(
        check_id=check_id,
        check_name=check_id,
        category="cost",
        sub_category="network",
        status="fail",
        severity=Severity(level="high" if score >= 800 else "medium", score=score),
        title=f"ELB signal: {check_id} ({lb_name})",
        message="test fixture",
        recommendation="",
        scope=Scope(
            cloud="aws",
            account_id=account_id,
            region=region,
            service="ElasticLoadBalancingV2",
            resource_type="load-balancer",
            resource_id=lb_name,
            resource_arn=(
                f"arn:aws:elasticloadbalancing:{region}:{account_id}:"
                f"loadbalancer/app/{lb_name}/1234567890abcdef"
            ),
        ),
        estimated_monthly_cost=monthly_cost,
        estimated_monthly_savings=monthly_cost,
        estimate_confidence=50,
        dimensions={
            "scheme": scheme,
            "lb_type": lb_type,
            "vpc_id": vpc_id,
            "subnet_ids": subnet_ids,
            "target_group_arns": target_group_arns,
        },
        issue_key={
            "check_id": check_id,
            "account_id": account_id,
            "region": region,
            "lb_name": lb_name,
        },
    )


def test_rule_emits_for_public_lb_with_multiple_ingress_chain_signals() -> None:
    """Emit the correlation when a public load balancer has stacked signals."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("elbv2_public_chain_emits")

    lb_name = "public-alb-001"
    drafts = [
        _draft_lb_signal(
            check_id="aws.elbv2.load.balancers.idle",
            account_id="123456789012",
            region="eu-west-1",
            lb_name=lb_name,
        ),
        _draft_lb_signal(
            check_id="aws.elbv2.load.balancers.no.healthy.targets",
            account_id="123456789012",
            region="eu-west-1",
            lb_name=lb_name,
            score=820,
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

    corr_rows = [r for r in rows if r.get("check_id") == "aws.elbv2.correlation.public.ingress.chain"]
    assert len(corr_rows) == 1
    assert corr_rows[0]["scope"]["resource_id"] == lb_name


def test_rule_does_not_emit_for_internal_lb() -> None:
    """Skip the correlation when the load balancer is not internet-facing."""

    corr = make_ctx()
    raw_dir, out_dir = _case_dirs("elbv2_public_chain_internal_no_emit")

    drafts = [
        _draft_lb_signal(
            check_id="aws.elbv2.load.balancers.idle",
            account_id="123456789012",
            region="eu-west-1",
            lb_name="internal-alb-001",
            scheme="internal",
        ),
        _draft_lb_signal(
            check_id="aws.elbv2.load.balancers.no.registered.targets",
            account_id="123456789012",
            region="eu-west-1",
            lb_name="internal-alb-001",
            scheme="internal",
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
    assert len([r for r in rows if r.get("check_id") == "aws.elbv2.correlation.public.ingress.chain"]) == 0


def test_rule_is_deterministic_for_same_input() -> None:
    """Produce identical correlated output for the same raw ELB inputs."""

    corr = make_ctx()
    raw_dir, _unused_out_dir = _case_dirs("elbv2_public_chain_deterministic")
    out_dir1 = raw_dir.parent / "corr1"
    out_dir2 = raw_dir.parent / "corr2"

    drafts = [
        _draft_lb_signal(
            check_id="aws.elbv2.load.balancers.no.registered.targets",
            account_id="123456789012",
            region="eu-west-1",
            lb_name="deterministic-alb",
        ),
        _draft_lb_signal(
            check_id="aws.elbv2.load.balancers.no.healthy.targets",
            account_id="123456789012",
            region="eu-west-1",
            lb_name="deterministic-alb",
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
