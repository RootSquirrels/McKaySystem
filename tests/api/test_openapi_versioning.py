"""Tests for OpenAPI generation and API versioned route aliases."""

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


def test_versioned_findings_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/findings` should behave like `/api/findings`."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = (sql, params)
        return []

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        _ = (sql, params)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/findings?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert "items" in body
    assert body.get("total") == 0


def test_openapi_public_endpoint_contains_versioned_servers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """OpenAPI spec should expose versioned + legacy API bases."""
    _disable_runtime_guards(monkeypatch)

    client = flask_app.app.test_client()
    resp = client.get("/openapi.json")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("openapi") == "3.0.3"
    servers = body.get("servers") or []
    urls = {str(s.get("url")) for s in servers if isinstance(s, dict)}
    assert "/api/v1" in urls
    assert "/api" in urls
    paths = body.get("paths") or {}
    assert "/findings" in paths
    assert "get" in (paths.get("/findings") or {})
    assert "/recommendations" in paths
    assert "get" in (paths.get("/recommendations") or {})
    assert "/recommendations/estimate" in paths
    assert "post" in (paths.get("/recommendations/estimate") or {})
    assert "/recommendations/preview" in paths
    assert "post" in (paths.get("/recommendations/preview") or {})
    assert "/remediations" in paths
    assert "get" in (paths.get("/remediations") or {})
    assert "/remediations/impact" in paths
    assert "get" in (paths.get("/remediations/impact") or {})
    assert "/remediations/request" in paths
    assert "post" in (paths.get("/remediations/request") or {})
    assert "/remediations/approve" in paths
    assert "post" in (paths.get("/remediations/approve") or {})


def test_versioned_recommendations_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/recommendations` should behave like `/api/recommendations`."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = (sql, params)
        return []

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        _ = (sql, params)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/recommendations?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    assert body.get("total") == 0


def test_versioned_recommendations_estimate_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/recommendations/estimate` should behave like `/api/recommendations/estimate`."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = (sql, params)
        return []

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        _ = (sql, params)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post("/api/v1/recommendations/estimate", json={"tenant_id": "acme", "workspace": "prod"})

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    assert body.get("mode") == "estimate"


def test_versioned_remediations_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/remediations` should behave like `/api/remediations`."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = (sql, params)
        return []

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        _ = (sql, params)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/remediations?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    assert body.get("total") == 0


def test_versioned_remediations_impact_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/remediations/impact` should behave like `/api/remediations/impact`."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = (sql, params)
        return []

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        _ = (sql, params)
        return {
            "n": 0,
            "actions_count": 0,
            "resolved_count": 0,
            "persistent_count": 0,
            "pending_count": 0,
            "failed_count": 0,
            "baseline_total_monthly_savings": 0.0,
            "realized_total_monthly_savings": 0.0,
        }

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/remediations/impact?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    summary = body.get("summary") or {}
    assert summary.get("actions_count") == 0


def test_versioned_remediations_request_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/remediations/request` should behave like `/api/remediations/request`."""
    _disable_runtime_guards(monkeypatch)
    execute_calls: list[tuple[str, Sequence[Any] | None]] = []
    fetch_calls = {"n": 0}

    def _fake_fetch_one(
        _conn: object, _sql: str, _params: Sequence[Any] | None = None
    ) -> dict[str, Any] | None:
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            return {
                "tenant_id": "acme",
                "workspace": "prod",
                "fingerprint": "fp-1",
                "check_id": "aws.ec2.instances.underutilized",
                "effective_state": "open",
                "service": "ec2",
            }
        if fetch_calls["n"] == 2:
            return None
        return {
            "tenant_id": "acme",
            "workspace": "prod",
            "action_id": "act-v1",
            "fingerprint": "fp-1",
            "check_id": "aws.ec2.instances.underutilized",
            "action_type": "rightsize",
            "status": "pending_approval",
            "action_payload": {},
            "dry_run": True,
            "reason": None,
            "requested_by": None,
            "approved_by": None,
            "rejected_by": None,
            "requested_at": "2026-02-15T10:00:00Z",
            "approved_at": None,
            "rejected_at": None,
            "updated_at": "2026-02-15T10:00:00Z",
            "version": 1,
        }

    def _fake_execute(_conn: object, sql: str, params: Sequence[Any] | None = None) -> None:
        execute_calls.append((sql, params))

    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)
    monkeypatch.setattr(flask_app, "execute_conn", _fake_execute)

    client = flask_app.app.test_client()
    resp = client.post(
        "/api/v1/remediations/request",
        json={"tenant_id": "acme", "workspace": "prod", "fingerprint": "fp-1", "action_id": "act-v1"},
    )

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    assert body.get("created") is True
    assert execute_calls


def test_versioned_openapi_alias_and_version_endpoint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Versioned aliases should exist for OpenAPI and version metadata routes."""
    _disable_runtime_guards(monkeypatch)

    client = flask_app.app.test_client()

    spec_resp = client.get("/api/v1/openapi.json")
    assert spec_resp.status_code == 200

    version_resp = client.get("/api/v1/version")
    assert version_resp.status_code == 200
    version_body = version_resp.get_json() or {}
    assert version_body.get("version") == "v1"
    assert version_body.get("prefix") == "/api/v1"


def test_versioned_latest_run_coverage_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/runs/latest/coverage` should expose latest run coverage summary."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any] | None:
        _ = params
        assert "run_coverage_summary" in sql
        return {
            "tenant_id": "acme",
            "workspace": "prod",
            "run_id": "run-1",
            "run_ts": "2026-03-17T09:00:00Z",
            "status": "ready",
            "coverage_pct": 84.21,
            "coverage_status": "degraded",
            "coverage_targets": 10,
            "coverage_failed": 2,
            "permission_gap_count": 1,
            "targets_total": 10,
            "assessed_total": 8,
            "assessed_with_findings": 3,
            "assessed_no_issue": 5,
            "assessment_failed": 2,
            "skipped_total": 0,
            "not_assessed_total": 0,
            "summary_permission_gap_count": 1,
            "summary_coverage_pct": 84.21,
            "summary_coverage_status": "degraded",
            "confidence": "medium",
        }

    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/runs/latest/coverage?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    run = body.get("run") or {}
    coverage = body.get("coverage") or {}
    assert run.get("coverage_pct") == 84.21
    assert run.get("coverage_status") == "degraded"
    assert coverage.get("targets_total") == 10
    assert coverage.get("assessment_failed") == 2
    assert coverage.get("confidence") == "medium"


def test_versioned_latest_run_coverage_checkers_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/runs/latest/coverage/checkers` should expose checker coverage rows."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = params
        assert "run_checker_coverage" in sql
        return [
            {
                "run_id": "run-1",
                "run_ts": "2026-03-17T09:00:00Z",
                "account_id": "123456789012",
                "region": "eu-west-1",
                "service": "ec2",
                "checker_id": "aws.ec2.idle.instances",
                "checker_scope": "regional",
                "status": "assessment_failed",
                "findings_count": 0,
                "duration_ms": 1234,
                "confidence": "low",
                "completeness_pct": None,
                "permission_gap_count": 1,
                "error_class": "missing_permission",
                "error_code": "AccessDenied",
                "error_message": "Denied",
                "skip_reason": None,
                "started_at": "2026-03-17T09:00:00Z",
                "finished_at": "2026-03-17T09:00:01Z",
            }
        ]

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/runs/latest/coverage/checkers?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    run = body.get("run") or {}
    items = body.get("items") or []
    assert run.get("run_id") == "run-1"
    assert len(items) == 1
    assert items[0].get("checker_id") == "aws.ec2.idle.instances"
    assert items[0].get("status") == "assessment_failed"
    assert items[0].get("permission_gap_count") == 1


def test_versioned_latest_run_coverage_issues_alias_works(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/v1/runs/latest/coverage/issues` should expose structured issue rows."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        _ = params
        assert "run_coverage_issues" in sql
        return [
            {
                "run_id": "run-1",
                "run_ts": "2026-03-17T09:00:00Z",
                "account_id": "123456789012",
                "region": "eu-west-1",
                "service": "ec2",
                "checker_id": "aws.ec2.idle.instances",
                "issue_type": "missing_permission",
                "operation": "DescribeInstances",
                "error_code": "AccessDenied",
                "message": "Denied",
                "is_retryable": False,
                "severity": "error",
                "payload": {"action": "ec2:DescribeInstances"},
                "created_at": "2026-03-17T09:00:01Z",
            }
        ]

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)

    client = flask_app.app.test_client()
    resp = client.get("/api/v1/runs/latest/coverage/issues?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    body = resp.get_json() or {}
    assert body.get("ok") is True
    run = body.get("run") or {}
    items = body.get("items") or []
    assert run.get("run_id") == "run-1"
    assert len(items) == 1
    assert items[0].get("issue_type") == "missing_permission"
    assert items[0].get("error_code") == "AccessDenied"
    assert items[0].get("is_retryable") is False
