"""Tests for recommendations API endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import apps.flask_api.blueprints.recommendations as recommendations_blueprint
import apps.flask_api.flask_app as flask_app
from apps.flask_api import auth_middleware
from services.rbac_service import AuthContext


class _DummyConn:
    """Minimal context manager returned by db_conn during unit tests."""

    def __enter__(self) -> _DummyConn:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return False

    def commit(self) -> None:
        return


def _disable_runtime_guards(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Disable auth/schema gates so request tests focus on API SQL behavior."""
    monkeypatch.setattr(flask_app, "_schema_gate_enabled", False)
    monkeypatch.setattr(flask_app, "_schema_gate_checked", True)
    monkeypatch.setattr(flask_app, "_API_BEARER_TOKEN", "")
    monkeypatch.setattr(flask_app, "db_conn", lambda: _DummyConn())
    monkeypatch.setattr(recommendations_blueprint, "db_conn", lambda: _DummyConn())
    monkeypatch.setattr(
        recommendations_blueprint,
        "fetch_all_dict_conn",
        lambda conn, sql, params=None: flask_app.fetch_all_dict_conn(conn, sql, params),  # type: ignore[no-untyped-def]
    )
    monkeypatch.setattr(
        recommendations_blueprint,
        "fetch_one_dict_conn",
        lambda conn, sql, params=None: flask_app.fetch_one_dict_conn(conn, sql, params),  # type: ignore[no-untyped-def]
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


def test_recommendations_query_uses_finding_current(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations` must query scoped rows from finding_current."""
    _disable_runtime_guards(monkeypatch)
    captured_sql: list[str] = []

    def _fake_fetch_all(_conn: object, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        _ = params
        captured_sql.append(sql)
        return []

    def _fake_fetch_one(_conn: object, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any]:
        _ = params
        captured_sql.append(sql)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")

    assert resp.status_code == 200
    sql_blob = "\n".join(captured_sql).lower()
    assert "from finding_current" in sql_blob
    assert "tenant_id = %s" in sql_blob
    assert "workspace = %s" in sql_blob
    assert "check_id = any(%s)" in sql_blob
    assert "effective_state = any(%s)" in sql_blob
    assert "left join runs" in sql_blob


def test_recommendations_response_is_enriched(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations` should return recommendation metadata and annualized savings."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-1",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 100.5,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "advice": "Downsize based on sustained utilization trend.",
                    "estimated": {
                        "confidence": 91,
                        "pricing_source": "snapshot",
                        "pricing_version": "aws_2026_02_01",
                    },
                    "dimensions": {
                        "instance_type": "m5.2xlarge",
                        "recommended_instance_type": "m5.xlarge",
                    },
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("ok") is True
    assert payload.get("total") == 1
    item = (payload.get("items") or [])[0]
    assert item.get("recommendation_type") == "rightsizing.ec2.instance"
    assert item.get("priority") == "p1"
    assert item.get("action_type") == "rightsize"
    assert item.get("action") == "Downsize EC2 instance from m5.2xlarge to m5.xlarge based on sustained utilization."
    assert item.get("checker_advice") == "Downsize based on sustained utilization trend."
    assert (item.get("target") or {}).get("kind") == "instance_type"
    assert (item.get("target") or {}).get("value") == "m5.xlarge"
    assert (item.get("current") or {}).get("value") == "m5.2xlarge"
    assert item.get("confidence") == 91
    assert item.get("confidence_label") == "high"
    confidence_model = item.get("confidence_model") or {}
    assert confidence_model.get("version") == "v1"
    assert confidence_model.get("overall_score") == 91
    assert confidence_model.get("overall_label") == "high"
    assert (confidence_model.get("issue") or {}).get("label") == "high"
    assert "checker_estimated_confidence_provided" in ((confidence_model.get("issue") or {}).get("factors") or [])
    assert (confidence_model.get("savings") or {}).get("label") == "high"
    assert "snapshot_pricing_source" in ((confidence_model.get("savings") or {}).get("factors") or [])
    assert (confidence_model.get("action_safety") or {}).get("label") == "high"
    assert "reversible_optimization_action" in (
        (confidence_model.get("action_safety") or {}).get("factors") or []
    )
    assert item.get("pricing_source") == "snapshot"
    assert item.get("pricing_version") == "aws_2026_02_01"
    assert item.get("estimated_monthly_savings") == 100.5
    assert item.get("estimated_annual_savings") == 1206.0
    assert item.get("actionability_score") > 0
    assert item.get("actionability_label") in {"medium", "high"}


def test_recommendations_response_includes_graph_package_context(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Recommendations should include bounded graph package context when available."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if "resource_graph_edges_current" in sql:
            return [
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "volume",
                    "neighbor_resource_name": "data-volume",
                    "neighbor_owner_hint": "team-storage",
                    "total_neighbors": 4,
                },
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "edge_type": "member_of",
                    "source_kind": "derived",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:vpc:subnet:subnet-123",
                    "neighbor_service": "vpc",
                    "neighbor_resource_type": "subnet",
                    "neighbor_resource_name": "app-subnet-a",
                    "neighbor_owner_hint": "",
                    "total_neighbors": 4,
                },
            ]
        return [
            {
                "fingerprint": "fp-graph",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 40.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "instance",
                        "resource_id": "i-123",
                    },
                    "dimensions": {
                        "instance_type": "m5.2xlarge",
                        "recommended_instance_type": "m5.xlarge",
                    },
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    item = (payload.get("items") or [])[0]
    assert item.get("resource_key") == "aws:111111111111:us-east-1:ec2:instance:i-123"
    graph_package = item.get("graph_package") or {}
    assert graph_package.get("package_kind") == "storage_lineage_package"
    assert graph_package.get("package_title") == "Validate storage lineage before cleanup"
    assert graph_package.get("related_resource_count") == 4
    assert graph_package.get("blast_radius") == "medium"
    assert graph_package.get("owner_hint") == "team-storage"
    assert graph_package.get("package_owner_hint") == "team-storage"
    assert graph_package.get("actionability_label") in {"medium", "high"}
    assert graph_package.get("related_services") == ["ec2", "vpc"]
    confidence_model = item.get("confidence_model") or {}
    assert (confidence_model.get("action_safety") or {}).get("label") in {"medium", "high"}
    assert "medium_blast_radius" in (
        (confidence_model.get("action_safety") or {}).get("factors") or []
    )
    assert "owner_hint_present" in (
        (confidence_model.get("action_safety") or {}).get("factors") or []
    )
    checklist = graph_package.get("dependency_checklist") or []
    assert "Confirm attached or recently related compute no longer requires this storage asset." in checklist
    assert "Verify instance lineage and mount expectations before cleanup." in checklist
    sample_related = graph_package.get("sample_related_resources") or []
    assert len(sample_related) == 2
    assert sample_related[0].get("resource_type") == "volume"
    assert item.get("owner_hint") == "team-storage"
    assert item.get("actionability_label") in {"medium", "high"}


def test_recommendations_response_uses_nat_specific_graph_package(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """NAT recommendations should expose a NAT-specific graph package title and checklist."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if "resource_graph_edges_current" in sql:
            return [
                {
                    "root_resource_key": "aws:111111111111:eu-west-1:vpc:nat_gateway:nat-123",
                    "edge_type": "routes_via",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:eu-west-1:vpc:subnet:subnet-123",
                    "neighbor_service": "vpc",
                    "neighbor_resource_type": "subnet",
                    "neighbor_resource_name": "private-a",
                    "total_neighbors": 2,
                }
            ]
        return [
            {
                "fingerprint": "fp-nat",
                "check_id": "aws.ec2.nat.gateways.idle",
                "service": "ec2",
                "severity": "high",
                "category": "cost",
                "title": "Idle NAT gateway",
                "estimated_monthly_savings": 32.4,
                "region": "eu-west-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "eu-west-1",
                        "service": "ec2",
                        "resource_type": "nat_gateway",
                        "resource_id": "nat-123",
                    },
                    "dimensions": {},
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    item = (payload.get("items") or [])[0]
    graph_package = item.get("graph_package") or {}
    assert graph_package.get("package_kind") == "nat_dependency_package"
    assert graph_package.get("package_title") == "Validate NAT routing dependencies before cleanup"
    checklist = graph_package.get("dependency_checklist") or []
    assert "Validate route paths that still traverse this NAT gateway." in checklist
    assert "Review impacted subnets and their outbound dependency paths." in checklist


def test_recommendations_response_uses_ingress_specific_graph_package(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Ingress-related recommendations should expose ingress-specific package context."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if "resource_graph_edges_current" in sql:
            return [
                {
                    "root_resource_key": "aws:111111111111:eu-west-1:elbv2:load_balancer:arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/test/123",
                    "edge_type": "routes_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:eu-west-1:elbv2:target_group:arn:aws:elasticloadbalancing:eu-west-1:111111111111:targetgroup/test/456",
                    "neighbor_service": "elbv2",
                    "neighbor_resource_type": "target_group",
                    "neighbor_resource_name": "tg-test",
                    "total_neighbors": 1,
                }
            ]
        return [
            {
                "fingerprint": "fp-elb",
                "check_id": "aws.lambda.functions.unused",
                "service": "lambda",
                "severity": "medium",
                "category": "cost",
                "title": "Unused function with ingress path",
                "estimated_monthly_savings": 5.0,
                "region": "eu-west-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "eu-west-1",
                        "service": "elbv2",
                        "resource_type": "load_balancer",
                        "resource_arn": "arn:aws:elasticloadbalancing:eu-west-1:111111111111:loadbalancer/app/test/123",
                    },
                    "dimensions": {},
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    item = (payload.get("items") or [])[0]
    graph_package = item.get("graph_package") or {}
    assert graph_package.get("package_kind") == "ingress_dependency_package"
    assert graph_package.get("package_title") == "Review ingress dependency chain before remediation"
    checklist = graph_package.get("dependency_checklist") or []
    assert "Validate target groups, listeners, and downstream compute before deleting or consolidating ingress." in checklist
    assert "Check whether traffic is still expected to route through attached target groups." in checklist


def test_recommendations_response_assigns_one_package_savings_owner(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Overlapping package members should expose one effective savings owner."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if "resource_graph_edges_current" in sql:
            return [
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "volume",
                    "neighbor_resource_name": "data-volume",
                    "total_neighbors": 1,
                },
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "instance",
                    "neighbor_resource_name": "app-1",
                    "total_neighbors": 1,
                },
            ]
        return [
            {
                "fingerprint": "fp-owner",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 80.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "instance",
                        "resource_id": "i-123",
                    },
                    "dimensions": {
                        "instance_type": "m5.2xlarge",
                        "recommended_instance_type": "m5.xlarge",
                    },
                },
            },
            {
                "fingerprint": "fp-suppressed",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "Related EBS-backed instance package member",
                "estimated_monthly_savings": 30.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "volume",
                        "resource_id": "vol-123",
                    },
                    "dimensions": {},
                },
            },
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 2}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    items = payload.get("items") or []
    owner = next(item for item in items if item.get("fingerprint") == "fp-owner")
    suppressed = next(item for item in items if item.get("fingerprint") == "fp-suppressed")

    assert owner.get("is_primary_package_savings_owner") is True
    assert owner.get("effective_estimated_monthly_savings") == 110.0
    assert owner.get("suppressed_by_fingerprint") is None
    owner_package = owner.get("graph_package") or {}
    assert owner_package.get("package_estimated_monthly_savings") == 110.0
    assert owner_package.get("savings_owner_fingerprint") == "fp-owner"
    assert owner_package.get("suppressed_fingerprints") == ["fp-suppressed"]

    assert suppressed.get("is_primary_package_savings_owner") is False
    assert suppressed.get("effective_estimated_monthly_savings") == 0.0
    assert suppressed.get("suppressed_by_fingerprint") == "fp-owner"
    suppressed_package = suppressed.get("graph_package") or {}
    assert suppressed_package.get("package_cluster_key") == owner_package.get("package_cluster_key")
    assert suppressed_package.get("savings_owner_fingerprint") == "fp-owner"


def test_recommendations_packages_view_groups_leaf_items(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`view=packages` should return one package object per clustered recommendation set."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if "resource_graph_edges_current" in sql:
            return [
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "volume",
                    "neighbor_resource_name": "data-volume",
                    "neighbor_owner_hint": "team-storage",
                    "total_neighbors": 1,
                },
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "instance",
                    "neighbor_resource_name": "app-1",
                    "neighbor_owner_hint": "team-storage",
                    "total_neighbors": 1,
                },
            ]
        return [
            {
                "fingerprint": "fp-owner",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 80.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "instance",
                        "resource_id": "i-123",
                    },
                    "dimensions": {
                        "instance_type": "m5.2xlarge",
                        "recommended_instance_type": "m5.xlarge",
                    },
                },
            },
            {
                "fingerprint": "fp-suppressed",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "Related package member",
                "estimated_monthly_savings": 30.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "volume",
                        "resource_id": "vol-123",
                    },
                    "dimensions": {},
                },
            },
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 2}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod&view=packages")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("view") == "packages"
    assert payload.get("total") == 1
    assert payload.get("leaf_total") == 2
    package = (payload.get("items") or [])[0]
    assert package.get("package_kind") == "storage_lineage_package"
    assert package.get("package_estimated_monthly_savings") == 110.0
    assert package.get("package_estimated_annual_savings") == 1320.0
    assert package.get("owner_hint") == "team-storage"
    assert package.get("member_count") == 2
    assert package.get("suppressed_member_count") == 1
    assert package.get("primary_fingerprint") == "fp-owner"
    assert package.get("fingerprints") == ["fp-owner", "fp-suppressed"]
    primary = package.get("primary_recommendation") or {}
    assert primary.get("fingerprint") == "fp-owner"
    members = package.get("member_recommendations") or []
    assert len(members) == 2


def test_recommendations_rejects_invalid_view(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations` should reject unsupported view values."""
    _disable_runtime_guards(monkeypatch)
    client = flask_app.app.test_client()

    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod&view=invalid")
    payload = resp.get_json() or {}

    assert resp.status_code == 400
    assert payload.get("ok") is False
    assert payload.get("error") == "bad_request"


def test_recommendations_checker_advice_falls_back_to_legacy_field(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`checker_advice` should use payload.recommendation when payload.advice is missing."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-legacy",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 10.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "recommendation": "Legacy checker recommendation text.",
                    "estimated": {"confidence": 50},
                    "dimensions": {},
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    item = (payload.get("items") or [])[0]
    assert item.get("checker_advice") == "Legacy checker recommendation text."


def test_recommendations_composite_uses_finding_current(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations/composite` should aggregate scoped data from finding_current."""
    _disable_runtime_guards(monkeypatch)
    captured_sql: list[str] = []

    def _fake_fetch_all(_conn: object, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        _ = params
        captured_sql.append(sql)
        return [
            {
                "group_key": "rightsizing.ec2.instance",
                "finding_count": 2,
                "total_monthly_savings": 250.0,
                "total_annual_savings": 3000.0,
            }
        ]

    def _fake_fetch_one(_conn: object, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any]:
        _ = params
        captured_sql.append(sql)
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get(
        "/api/recommendations/composite?tenant_id=acme&workspace=prod&group_by=recommendation_type"
    )
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("ok") is True
    assert payload.get("group_by") == "recommendation_type"
    assert payload.get("total") == 1
    sql_blob = "\n".join(captured_sql).lower()
    assert "from finding_current" in sql_blob
    assert "group by group_key" in sql_blob
    assert "aws.ec2.instances.underutilized" in sql_blob


def test_recommendations_composite_rejects_invalid_group_by(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations/composite` should return 400 for invalid group_by values."""
    _disable_runtime_guards(monkeypatch)
    client = flask_app.app.test_client()

    resp = client.get("/api/recommendations/composite?tenant_id=acme&workspace=prod&group_by=invalid")
    payload = resp.get_json() or {}

    assert resp.status_code == 400
    assert payload.get("ok") is False
    assert payload.get("error") == "bad_request"


def test_recommendations_response_uses_run_metadata_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Recommendations should read pricing metadata from run metadata when payload lacks it."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-1",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 100.5,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {},
                "run_meta": {"pricing_source": "snapshot", "pricing_version": "aws_2026_04_01"},
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}
    item = (payload.get("items") or [])[0]

    assert resp.status_code == 200
    assert item.get("confidence") == 78
    confidence_model = item.get("confidence_model") or {}
    assert confidence_model.get("version") == "v1"
    assert confidence_model.get("overall_score") == 78
    assert item.get("pricing_source") == "snapshot"
    assert item.get("pricing_version") == "aws_2026_04_01"


def test_recommendations_query_excludes_access_denied_verification_rows(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Access-denied verification findings should stay out of recommendations."""
    _disable_runtime_guards(monkeypatch)
    captured_sql: list[str] = []

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        captured_sql.append(sql)
        return []

    def _fake_fetch_one(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        captured_sql.append(sql)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod&check_id=aws.s3.governance.lifecycle.missing")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("ok") is True
    sql_blob = "\n".join(captured_sql).lower()
    assert "cannot verify" in sql_blob
    assert "access_denied" in sql_blob


def test_recommendations_response_ri_coverage_gap_is_enriched(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """RI coverage-gap recommendations should expose actionable target/current values."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-ri-gap",
                "check_id": "aws.ec2.ri.coverage.gap",
                "service": "ec2",
                "severity": "high",
                "category": "cost",
                "title": "RI coverage gap",
                "estimated_monthly_savings": 43.8,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "dimensions": {
                        "instance_type": "m5.large",
                        "uncovered_count": "2",
                        "coverage_pct": "33.33",
                        "target_coverage_pct": "90.00",
                    }
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    item = (payload.get("items") or [])[0]
    assert item.get("recommendation_type") == "commitment.ec2.ri.coverage"
    assert item.get("action_type") == "purchase"
    assert (item.get("current") or {}).get("value") == "33.33"
    assert (item.get("target") or {}).get("value") == "90.00"
    assert "m5.large" in str(item.get("action") or "")


def test_recommendations_response_savings_plan_coverage_gap_is_enriched(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Savings Plan coverage-gap recommendations should expose hourly commitment guidance."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-sp-gap",
                "check_id": "aws.ec2.savings.plans.coverage.gap",
                "service": "ec2",
                "severity": "high",
                "category": "cost",
                "title": "Savings Plan coverage gap",
                "estimated_monthly_savings": 36.5,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "dimensions": {
                        "estimated_demand_usd_per_hour": "0.2000",
                        "committed_usd_per_hour": "0.0000",
                        "uncovered_usd_per_hour": "0.2000",
                    }
                },
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.get("/api/recommendations?tenant_id=acme&workspace=prod")
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    item = (payload.get("items") or [])[0]
    assert item.get("recommendation_type") == "commitment.ec2.savings_plan.coverage"
    assert item.get("action_type") == "purchase"
    assert (item.get("current") or {}).get("value") == "0.0000"
    assert (item.get("target") or {}).get("value") == "0.2000"
    assert "$0.2000/hr" in str(item.get("action") or "")


def test_recommendations_estimate_is_scoped_and_uses_finding_current(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations/estimate` should query finding_current with scope + fingerprint filter."""
    _disable_runtime_guards(monkeypatch)
    captured_sql: list[str] = []

    def _fake_fetch_all(_conn: object, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        _ = params
        captured_sql.append(sql)
        return []

    def _fake_fetch_one(_conn: object, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any]:
        _ = params
        captured_sql.append(sql)
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post(
        "/api/recommendations/estimate",
        json={
            "tenant_id": "acme",
            "workspace": "prod",
            "fingerprints": ["fp-1", "fp-2"],
        },
    )

    assert resp.status_code == 200
    sql_blob = "\n".join(captured_sql).lower()
    assert "from finding_current" in sql_blob
    assert "tenant_id = %s" in sql_blob
    assert "workspace = %s" in sql_blob
    assert "fingerprint = any(%s)" in sql_blob
    assert "check_id = any(%s)" in sql_blob


def test_recommendations_estimate_returns_totals_and_warnings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Estimate response should include deterministic totals and risk warnings."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-1",
                "check_id": "aws.ec2.nat.gateways.idle",
                "service": "ec2",
                "severity": "high",
                "category": "cost",
                "title": "Idle NAT gateway",
                "estimated_monthly_savings": 200.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "estimated": {"confidence": 88},
                    "dimensions": {"pricing_version": "aws_2026_02_01"},
                },
            },
            {
                "fingerprint": "fp-2",
                "check_id": "aws.s3.governance.lifecycle.missing",
                "service": "s3",
                "severity": "medium",
                "category": "cost",
                "title": "Lifecycle missing",
                "estimated_monthly_savings": 50.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "estimated": {"confidence": 61},
                    "dimensions": {"pricing_version": "aws_2026_02_01"},
                },
            },
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 2}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post(
        "/api/recommendations/estimate",
        json={
            "tenant_id": "acme",
            "workspace": "prod",
            "fingerprints": ["fp-1", "fp-missing", "fp-2"],
        },
    )
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("ok") is True
    assert payload.get("mode") == "estimate"
    assert payload.get("pricing_version") == "aws_2026_02_01"
    assert payload.get("pricing_versions") == ["aws_2026_02_01"]
    assert payload.get("total") == 2
    assert payload.get("selected_count") == 2
    totals = payload.get("totals") or {}
    assert totals.get("estimated_monthly_savings") == 250.0
    assert totals.get("estimated_annual_savings") == 3000.0
    warnings = payload.get("risk_warnings") or []
    warning_codes = {str(w.get("code")) for w in warnings}
    assert "approval_required" in warning_codes
    assert "missing_or_ineligible" in warning_codes


def test_recommendations_estimate_uses_effective_package_savings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Estimate totals should follow package-level savings ownership when items overlap."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if "resource_graph_edges_current" in sql:
            return [
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "volume",
                    "neighbor_resource_name": "data-volume",
                    "total_neighbors": 1,
                },
                {
                    "root_resource_key": "aws:111111111111:us-east-1:ec2:volume:vol-123",
                    "edge_type": "attached_to",
                    "source_kind": "api_direct",
                    "confidence": "high",
                    "neighbor_resource_key": "aws:111111111111:us-east-1:ec2:instance:i-123",
                    "neighbor_service": "ec2",
                    "neighbor_resource_type": "instance",
                    "neighbor_resource_name": "app-1",
                    "total_neighbors": 1,
                },
            ]
        return [
            {
                "fingerprint": "fp-owner",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 80.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "instance",
                        "resource_id": "i-123",
                    },
                    "dimensions": {},
                },
            },
            {
                "fingerprint": "fp-suppressed",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "Related package member",
                "estimated_monthly_savings": 30.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {
                    "scope": {
                        "account_id": "111111111111",
                        "region": "us-east-1",
                        "service": "ec2",
                        "resource_type": "volume",
                        "resource_id": "vol-123",
                    },
                    "dimensions": {},
                },
            },
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 2}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post("/api/recommendations/estimate", json={"tenant_id": "acme", "workspace": "prod"})
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    totals = payload.get("totals") or {}
    assert totals.get("estimated_monthly_savings") == 110.0
    assert totals.get("estimated_annual_savings") == 1320.0


def test_recommendations_preview_alias_points_to_estimate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`/api/recommendations/preview` should be an alias of estimate semantics."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return []

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 0}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post("/api/recommendations/preview", json={"tenant_id": "acme", "workspace": "prod"})
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("ok") is True
    assert payload.get("mode") == "estimate"


def test_recommendations_estimate_pricing_version_mixed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Estimate should expose mixed pricing versions when selected items differ."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-1",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 10.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {"dimensions": {"pricing_version": "aws_2026_02_01"}},
            },
            {
                "fingerprint": "fp-2",
                "check_id": "aws.rds.storage.overprovisioned",
                "service": "rds",
                "severity": "medium",
                "category": "rightsizing",
                "title": "RDS storage overprovisioned",
                "estimated_monthly_savings": 20.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {"dimensions": {"pricing_version": "aws_2026_03_01"}},
            },
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 2}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post("/api/recommendations/estimate", json={"tenant_id": "acme", "workspace": "prod"})
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("pricing_version") == "mixed"
    assert payload.get("pricing_versions") == ["aws_2026_02_01", "aws_2026_03_01"]


def test_recommendations_estimate_pricing_version_from_run_metadata(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Estimate should use run metadata pricing_version when payload does not provide one."""
    _disable_runtime_guards(monkeypatch)

    def _fake_fetch_all(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": "fp-1",
                "check_id": "aws.ec2.instances.underutilized",
                "service": "ec2",
                "severity": "medium",
                "category": "rightsizing",
                "title": "EC2 instance underutilized",
                "estimated_monthly_savings": 10.0,
                "region": "us-east-1",
                "account_id": "111111111111",
                "detected_at": "2026-02-14T00:00:00Z",
                "effective_state": "open",
                "payload": {},
                "run_meta": {"pricing_version": "aws_2026_05_01"},
            }
        ]

    def _fake_fetch_one(_conn: object, _sql: str, _params: Sequence[Any] | None = None) -> dict[str, Any]:
        return {"n": 1}

    monkeypatch.setattr(flask_app, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(flask_app, "fetch_one_dict_conn", _fake_fetch_one)

    client = flask_app.app.test_client()
    resp = client.post("/api/recommendations/estimate", json={"tenant_id": "acme", "workspace": "prod"})
    payload = resp.get_json() or {}

    assert resp.status_code == 200
    assert payload.get("pricing_version") == "aws_2026_05_01"
    assert payload.get("pricing_versions") == ["aws_2026_05_01"]


def test_recommendations_estimate_rejects_invalid_fingerprints_type(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Estimate endpoint should validate fingerprints payload type."""
    _disable_runtime_guards(monkeypatch)
    client = flask_app.app.test_client()

    resp = client.post(
        "/api/recommendations/estimate",
        json={"tenant_id": "acme", "workspace": "prod", "fingerprints": 123},
    )
    payload = resp.get_json() or {}

    assert resp.status_code == 400
    assert payload.get("ok") is False
    assert payload.get("error") == "bad_request"
