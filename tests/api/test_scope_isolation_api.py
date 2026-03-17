"""API scope-isolation regression tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import apps.flask_api.blueprints.findings as findings_blueprint
import apps.flask_api.blueprints.groups as groups_blueprint
import apps.flask_api.blueprints.recommendations as recommendations_blueprint
import apps.flask_api.blueprints.remediations as remediations_blueprint
import apps.flask_api.flask_app as flask_app
from apps.flask_api import auth_middleware
from services.rbac_service import AuthContext


class _DummyConn:
    """Minimal context manager returned by db_conn during unit tests."""

    def __enter__(self) -> _DummyConn:
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:  # type: ignore[no-untyped-def]
        return False

    def commit(self) -> None:
        return


def _disable_runtime_guards(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Disable auth/schema guards so tests focus on tenant/workspace isolation."""
    monkeypatch.setattr(flask_app, "_schema_gate_enabled", False)
    monkeypatch.setattr(flask_app, "_schema_gate_checked", True)
    monkeypatch.setattr(flask_app, "_API_BEARER_TOKEN", "")
    monkeypatch.setattr(flask_app, "db_conn", lambda: _DummyConn())

    for module in (
        findings_blueprint,
        groups_blueprint,
        recommendations_blueprint,
        remediations_blueprint,
    ):
        monkeypatch.setattr(module, "db_conn", lambda: _DummyConn())
        if hasattr(module, "fetch_all_dict_conn"):
            monkeypatch.setattr(
                module,
                "fetch_all_dict_conn",
                lambda conn, sql, params=None: flask_app.fetch_all_dict_conn(conn, sql, params),  # type: ignore[no-untyped-def]
            )
        if hasattr(module, "fetch_one_dict_conn"):
            monkeypatch.setattr(
                module,
                "fetch_one_dict_conn",
                lambda conn, sql, params=None: flask_app.fetch_one_dict_conn(conn, sql, params),  # type: ignore[no-untyped-def]
            )
        if hasattr(module, "execute_conn"):
            monkeypatch.setattr(
                module,
                "execute_conn",
                lambda conn, sql, params=None: flask_app.execute_conn(conn, sql, params),  # type: ignore[no-untyped-def]
            )

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


def test_findings_queries_keep_request_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Findings reads must keep tenant/workspace from the request in every SQL call."""
    _disable_runtime_guards(monkeypatch)
    captured_params: list[Sequence[Any] | None] = []

    def _fake_fetch_all(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured_params.append(params)
        return []

    def _fake_fetch_one(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured_params.append(params)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/findings?tenant_id=globex&workspace=dev&state=open")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("tenant_id") == "globex"
    assert payload.get("workspace") == "dev"
    assert captured_params
    assert all(params is not None and params[0] == "globex" and params[1] == "dev" for params in captured_params)


def test_recommendations_queries_keep_request_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Recommendations reads must remain scoped to the request tenant/workspace."""
    _disable_runtime_guards(monkeypatch)
    captured_params: list[Sequence[Any] | None] = []

    def _fake_fetch_all(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured_params.append(params)
        return []

    def _fake_fetch_one(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured_params.append(params)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=globex&workspace=stage")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("tenant_id") == "globex"
    assert payload.get("workspace") == "stage"
    assert captured_params
    assert all(params is not None and params[0] == "globex" and params[1] == "stage" for params in captured_params)


def test_group_detail_queries_keep_request_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Group detail reads must not leak data across tenant/workspace boundaries."""
    _disable_runtime_guards(monkeypatch)
    captured_params: list[Sequence[Any] | None] = []

    def _fake_fetch_all(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured_params.append(params)
        return []

    call_no = {"n": 0}

    def _fake_fetch_one(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured_params.append(params)
        call_no["n"] += 1
        if call_no["n"] == 1:
            return {
                "group_key": "grp-1",
                "title": "Example",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "category": "rightsizing",
                "finding_count": 0,
                "total_savings": 0.0,
            }
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/groups/grp-1?tenant_id=globex&workspace=stage")

    assert resp.status_code == 200
    assert captured_params
    assert all(params is not None and params[0] == "globex" and params[1] == "stage" for params in captured_params)


def test_remediations_impact_queries_keep_request_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Remediation impact reads must remain tenant/workspace scoped for all queries."""
    _disable_runtime_guards(monkeypatch)
    captured_params: list[Sequence[Any] | None] = []

    def _fake_fetch_all(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured_params.append(params)
        return []

    call_no = {"n": 0}

    def _fake_fetch_one(
        _conn: object, _sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured_params.append(params)
        call_no["n"] += 1
        if call_no["n"] == 1:
            return {"n": 0}
        return {
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
    resp = client.get("/api/remediations/impact?tenant_id=globex&workspace=stage")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("tenant_id") == "globex"
    assert payload.get("workspace") == "stage"
    assert captured_params
    assert all(params is not None and params[0] == "globex" and params[1] == "stage" for params in captured_params)


def test_remediation_request_cannot_see_other_scope_finding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Action creation must return 404 when the fingerprint only exists in another scope."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_finding_for_request(
        _conn: object,
        *,
        tenant_id: str,
        workspace: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        assert tenant_id == "globex"
        assert workspace == "stage"
        assert fingerprint == "fp-other-tenant"
        return None

    monkeypatch.setattr(remediations_blueprint, "_fetch_finding_for_request", _fake_fetch_finding_for_request)

    client = flask_app.app.test_client()
    resp = client.post(
        "/api/remediations/request",
        json={
            "tenant_id": "globex",
            "workspace": "stage",
            "fingerprint": "fp-other-tenant",
            "requested_by": "tester@globex.io",
        },
    )
    payload = resp.get_json() or {}

    assert resp.status_code == 404
    assert payload.get("error") == "not_found"


def test_finding_owner_update_cannot_see_other_scope_finding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Owner mutation must return 404 when the finding exists only outside the requested scope."""
    _disable_runtime_guards(monkeypatch)

    def _fake_finding_exists(
        _conn: object,
        *,
        tenant_id: str,
        workspace: str,
        fingerprint: str,
    ) -> bool:
        assert tenant_id == "globex"
        assert workspace == "stage"
        assert fingerprint == "fp-other-tenant"
        return False

    monkeypatch.setattr(findings_blueprint, "_finding_exists", _fake_finding_exists)

    client = flask_app.app.test_client()
    resp = client.put(
        "/api/findings/fp-other-tenant/owner",
        json={
            "tenant_id": "globex",
            "workspace": "stage",
            "owner_email": "owner@globex.io",
            "updated_by": "tester@globex.io",
        },
    )
    payload = resp.get_json() or {}

    assert resp.status_code == 404
    assert payload.get("error") == "not_found"
