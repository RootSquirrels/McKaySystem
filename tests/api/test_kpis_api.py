"""Tests for KPI summary API endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from apps.flask_api import auth_middleware, flask_app
from services.rbac_service import AuthContext


class _DummyConn:
    def __enter__(self) -> _DummyConn:
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:  # type: ignore[no-untyped-def]
        return False

    def commit(self) -> None:
        return


def _disable_runtime_guards(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(flask_app, "_schema_gate_enabled", False)
    monkeypatch.setattr(flask_app, "_schema_gate_checked", True)
    monkeypatch.setattr(flask_app, "_API_BEARER_TOKEN", "")
    monkeypatch.setattr(flask_app, "db_conn", lambda: _DummyConn())
    monkeypatch.setattr(
        auth_middleware,
        "authenticate_request",
        lambda: AuthContext(
            tenant_id="acme",
            workspace="prod",
            user_id="u-test",
            email="tester@acme.io",
            full_name="RBAC Test",
            is_superadmin=False,
            auth_method="session",
            permissions=frozenset({"admin:full"}),
        ),
    )


def test_kpis_initial_value_returns_explicit_metric_families(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """KPI endpoint should keep findings, recommendations, realized, and coverage separate."""

    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any] | None:
        _ = params
        if "FROM (" in sql and "finding_presence p1" in sql:
            return {"n": 3}
        if "FROM (" in sql and "finding_presence p0" in sql:
            return {"n": 1}
        if "FROM runs" in sql and "ORDER BY run_ts DESC" in sql and "run_coverage_summary" not in sql:
            return {"run_id": "run-123", "run_ts": "2026-03-18T12:00:00Z"}
        if "FROM finding_current" in sql and "needs_attention_count" in sql:
            return {
                "open_findings_count": 12,
                "needs_attention_count": 4,
                "estimated_monthly_savings": 110.0,
            }
        if "FROM finding_current" in sql and "eligible_recommendations_count" in sql:
            return {
                "eligible_recommendations_count": 7,
                "priority_p1_count": 3,
                "estimated_monthly_savings": 85.0,
            }
        if "FROM remediation_impact" in sql:
            return {
                "actions_count": 5,
                "fully_realized_count": 2,
                "partial_realization_count": 1,
                "no_realization_count": 1,
                "pending_count": 1,
                "failed_count": 0,
                "baseline_total_monthly_savings": 120.0,
                "realized_total_monthly_savings": 70.0,
                "estimated_not_realized_monthly_savings": 50.0,
            }
        if "FROM run_coverage_summary" in sql:
            return {
                "coverage_pct": 84.2,
                "coverage_status": "degraded",
                "permission_gap_count": 2,
                "assessment_failed": 1,
                "targets_total": 100,
                "assessed_total": 84,
                "confidence": "medium",
                "run_id": "run-123",
                "run_ts": "2026-03-18T12:00:00Z",
            }
        return None

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = params
        if "FROM runs" in sql and "status = 'ready'" in sql:
            return [
                {"run_id": "run-123", "run_ts": "2026-03-18T12:00:00Z"},
                {"run_id": "run-122", "run_ts": "2026-03-17T12:00:00Z"},
            ]
        if "FROM finding_latest fl" in sql:
            return [
                {"run_id": "run-123", "eligible_count": 7, "estimated_monthly_savings": 85.0},
                {"run_id": "run-122", "eligible_count": 5, "estimated_monthly_savings": 60.0},
            ]
        if "FROM run_coverage_summary" in sql and "run_id = ANY" in sql:
            return [
                {
                    "run_id": "run-123",
                    "coverage_pct": 84.2,
                    "assessment_failed": 1,
                    "permission_gap_count": 2,
                    "coverage_status": "degraded",
                },
                {
                    "run_id": "run-122",
                    "coverage_pct": 80.0,
                    "assessment_failed": 0,
                    "permission_gap_count": 1,
                    "coverage_status": "healthy",
                },
            ]
        return []

    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)
    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)

    client = flask_app.app.test_client()
    response = client.get("/api/v1/kpis/initial-value?tenant_id=acme&workspace=prod")

    assert response.status_code == 200
    body = response.get_json() or {}
    assert body.get("ok") is True
    assert body.get("tenant_id") == "acme"
    assert body.get("workspace") == "prod"
    assert (body.get("latest_run") or {}).get("run_id") == "run-123"

    kpis = body.get("kpis") or {}
    findings = kpis.get("findings") or {}
    recommendations = kpis.get("recommendations") or {}
    realized = kpis.get("realized") or {}
    coverage = kpis.get("coverage") or {}
    trend = body.get("trend") or {}

    assert findings.get("open_findings_count") == 12
    assert findings.get("needs_attention_count") == 4
    assert findings.get("estimated_monthly_savings") == 110.0

    assert recommendations.get("eligible_recommendations_count") == 7
    assert recommendations.get("priority_p1_count") == 3
    assert recommendations.get("estimated_monthly_savings") == 85.0

    assert realized.get("actions_count") == 5
    assert realized.get("realized_total_monthly_savings") == 70.0
    assert realized.get("estimated_not_realized_monthly_savings") == 50.0
    assert realized.get("realization_rate_pct") == (70.0 / 120.0) * 100.0

    assert coverage.get("coverage_pct") == 84.2
    assert coverage.get("coverage_status") == "degraded"
    assert coverage.get("permission_gap_count") == 2

    assert (trend.get("latest_run") or {}).get("run_id") == "run-123"
    assert (trend.get("previous_run") or {}).get("run_id") == "run-122"
    assert (trend.get("findings") or {}).get("new_count") == 3
    assert (trend.get("findings") or {}).get("disappeared_count") == 1
    assert (trend.get("findings") or {}).get("net_change") == 2
    assert (trend.get("recommendations") or {}).get("eligible_count_delta") == 2
    assert (trend.get("recommendations") or {}).get("estimated_monthly_savings_delta") == 25.0
    assert (trend.get("coverage") or {}).get("coverage_pct_delta") == 4.2
    assert (trend.get("coverage") or {}).get("assessment_failed_delta") == 1
    assert (trend.get("coverage") or {}).get("permission_gap_delta") == 1

    notes = body.get("notes") or []
    assert len(notes) >= 1
