"""Tests for API route reliability/performance logging."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from apps.flask_api import auth_middleware, flask_app
from services.rbac_service import AuthContext


class _DummyConn:
    """Minimal context manager returned by db_conn during unit tests."""

    def __enter__(self) -> "_DummyConn":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return False

    def commit(self) -> None:
        return


def _disable_runtime_guards(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Disable auth/schema gates so request tests focus on route instrumentation."""
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


def test_findings_route_emits_perf_log(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Tracked routes should emit a dedicated api_route_perf event."""
    _disable_runtime_guards(monkeypatch)
    events: list[tuple[str, str, dict[str, Any]]] = []

    def _fake_fetch_all(
        _conn: object, _sql: str, _params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        return [{"fingerprint": "fp-1"}]

    def _fake_fetch_one(
        _conn: object, _sql: str, _params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        return {"n": 1}

    ticks = iter((100.0, 100.3))
    monkeypatch.setattr(flask_app.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)
    monkeypatch.setattr(
        flask_app,
        "_log",
        lambda level, event, fields: events.append((level, event, dict(fields))),
    )

    client = flask_app.app.test_client()
    resp = client.get("/api/findings?tenant_id=acme&workspace=prod&limit=25&offset=10&q=nat")

    assert resp.status_code == 200
    perf_events = [entry for entry in events if entry[1] == "api_route_perf"]
    assert len(perf_events) == 1
    level, _event, fields = perf_events[0]
    assert level == "INFO"
    assert fields["route_key"] == "/api/findings"
    assert fields["slo_ms"] == 500
    assert fields["ms"] == 299
    assert fields["limit"] == "25"
    assert fields["offset"] == "10"
    assert fields["has_q"] is True
    assert fields["items_count"] == 1
    assert fields["total"] == 1


def test_recommendations_route_logs_slo_breach(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Tracked routes should emit a breach event when latency exceeds the configured SLO."""
    _disable_runtime_guards(monkeypatch)
    events: list[tuple[str, str, dict[str, Any]]] = []

    def _fake_fetch_all(
        _conn: object, _sql: str, _params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        return []

    def _fake_fetch_one(
        _conn: object, _sql: str, _params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        return {"n": 0}

    ticks = iter((200.0, 200.9))
    monkeypatch.setattr(flask_app.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)
    monkeypatch.setattr(
        flask_app,
        "_log",
        lambda level, event, fields: events.append((level, event, dict(fields))),
    )

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    perf_events = [entry for entry in events if entry[1] == "api_route_perf"]
    breach_events = [entry for entry in events if entry[1] == "api_route_slo_breach"]
    assert len(perf_events) == 1
    assert len(breach_events) == 1
    assert breach_events[0][0] == "WARN"
    assert breach_events[0][2]["route_key"] == "/api/recommendations"
    assert breach_events[0][2]["slo_ms"] == 500
    assert breach_events[0][2]["ms"] == 900
