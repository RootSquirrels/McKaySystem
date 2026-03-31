"""Unit tests for runner coverage permission-gap classification."""

from __future__ import annotations

from apps.worker.runner import _is_permission_gap_finding, _permission_gap_findings_count


def test_is_permission_gap_finding_detects_missing_permission_check_id() -> None:
    """Permission findings should be recognized from stable check_id suffixes."""
    record = {
        "check_id": "aws.elbv2.load.balancers.missing.permission",
        "title": "Missing ELB permission for listeners",
        "message": "Access denied on elbv2:DescribeListeners.",
    }

    assert _is_permission_gap_finding(record) is True


def test_is_permission_gap_finding_detects_access_error_check_id() -> None:
    """Access-error findings should contribute to coverage permission gaps."""
    record = {
        "check_id": "aws.elbv2.load.balancers.access.error",
        "title": "Unable to list load balancers",
        "message": "Unable to list ELBv2 load balancers (AccessDenied).",
    }

    assert _is_permission_gap_finding(record) is True


def test_permission_gap_findings_count_ignores_regular_findings() -> None:
    """Only permission-oriented findings should increase the coverage gap count."""
    records = [
        {
            "check_id": "aws.elbv2.load.balancers.idle",
            "title": "Idle load balancer",
            "message": "Load balancer appears idle.",
        },
        {
            "check_id": "aws.elbv2.load.balancers.missing.permission",
            "title": "Missing ELB permission for tags",
            "message": "Access denied on elbv2:DescribeTags.",
        },
        {
            "check_id": "aws.elbv2.load.balancers.access.error",
            "title": "Unable to list load balancers",
            "message": "Unable to list ELBv2 load balancers (AccessDenied).",
        },
    ]

    assert _permission_gap_findings_count(records) == 2
