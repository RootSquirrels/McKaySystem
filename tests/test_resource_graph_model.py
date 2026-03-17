"""Tests for deterministic resource graph artifact helpers."""

from __future__ import annotations

from pathlib import Path

from apps.worker.resource_graph_model import (
    GRAPH_DIRNAME,
    build_graph_from_findings,
    load_graph_bundle,
    ResourceGraphNode,
    write_graph_bundle,
)


def test_build_graph_from_findings_derives_expected_nodes_and_edges() -> None:
    """Graph builder should derive deterministic nodes and relationship edges."""
    findings = [
        {
            "title": "Underutilized instance",
            "check_id": "aws.ec2.instances.underutilized",
            "severity": {"level": "medium"},
            "scope": {
                "account_id": "123456789012",
                "region": "eu-west-3",
                "service": "ec2",
                "resource_type": "instance",
                "resource_id": "i-12345678",
                "resource_arn": "",
            },
            "payload": {
                "resource_name": "compute-node",
                "dimensions": {
                    "subnet_id": "subnet-12345678",
                    "vpc_id": "vpc-12345678",
                    "security_group_ids": "sg-12345678",
                    "attached_volume_ids": "vol-12345678",
                },
            },
        }
    ]

    nodes, edges = build_graph_from_findings(
        findings,
        tenant_id="acme",
        workspace="prod",
        run_id="run-1",
    )

    resource_keys = [item.resource_key for item in nodes]
    edge_types = sorted(item.edge_type for item in edges)

    assert resource_keys == sorted(
        [
            "aws:123456789012:eu-west-3:ec2:instance:i-12345678",
            "aws:123456789012:eu-west-3:ec2:security_group:sg-12345678",
            "aws:123456789012:eu-west-3:ec2:volume:vol-12345678",
            "aws:123456789012:eu-west-3:vpc:subnet:subnet-12345678",
            "aws:123456789012:eu-west-3:vpc:vpc:vpc-12345678",
        ]
    )
    assert edge_types == ["attached_to", "member_of", "member_of", "member_of", "secured_by"]


def test_graph_bundle_round_trip(tmp_path: Path) -> None:
    """Graph bundles should round-trip through JSONL artifacts."""
    nodes, edges = build_graph_from_findings(
        [
            {
                "title": "Bucket lifecycle missing",
                "check_id": "aws.s3.lifecycle.missing",
                "severity": "low",
                "scope": {
                    "account_id": "123456789012",
                    "region": "eu-west-3",
                    "service": "s3",
                    "resource_type": "bucket",
                    "resource_id": "my-bucket",
                },
                "payload": {},
            }
        ],
        tenant_id="acme",
        workspace="prod",
        run_id="run-2",
    )

    graph_dir = write_graph_bundle(tmp_path, nodes=nodes, edges=edges)
    loaded_nodes, loaded_edges = load_graph_bundle(graph_dir)

    assert graph_dir == tmp_path / GRAPH_DIRNAME
    assert loaded_nodes == nodes
    assert loaded_edges == edges


def test_resource_graph_model_wrapper_exports_node_type() -> None:
    """Compatibility wrapper should still export graph dataclasses."""
    assert ResourceGraphNode.__name__ == "ResourceGraphNode"


def test_build_graph_from_findings_derives_nat_and_elb_relationships() -> None:
    """Graph builder should derive NAT and ELB relationships from emitted finding fields."""
    findings = [
        {
            "title": "Idle NAT Gateway",
            "check_id": "aws.ec2.nat.gateways.idle",
            "severity": "low",
            "issue_key": {"nat_gateway_id": "nat-12345678"},
            "scope": {
                "account_id": "123456789012",
                "region": "eu-west-3",
                "service": "ec2",
                "resource_type": "nat-gateway",
                "resource_id": "nat-12345678",
                "resource_arn": "",
            },
            "payload": {
                "dimensions": {
                    "subnet_id": "subnet-12345678",
                    "vpc_id": "vpc-12345678",
                    "routed_subnet_ids": "subnet-87654321",
                }
            },
        },
        {
            "title": "Idle load balancer",
            "check_id": "aws.elbv2.load.balancers.idle",
            "severity": "medium",
            "issue_key": {
                "lb_arn": "arn:aws:elasticloadbalancing:eu-west-3:123456789012:loadbalancer/app/demo/50dc6c495c0c9188"
            },
            "scope": {
                "account_id": "123456789012",
                "region": "eu-west-3",
                "service": "elbv2",
                "resource_type": "load_balancer",
                "resource_id": "demo-alb",
                "resource_arn": "",
            },
            "payload": {
                "dimensions": {
                    "vpc_id": "vpc-12345678",
                    "subnet_ids": "subnet-a,subnet-b",
                    "target_group_arns": (
                        "arn:aws:elasticloadbalancing:eu-west-3:123456789012:targetgroup/demo/6d0ecf831eec9f09,"
                        "arn:aws:elasticloadbalancing:eu-west-3:123456789012:targetgroup/demo2/6d0ecf831eec9f10"
                    ),
                }
            },
        },
    ]

    nodes, edges = build_graph_from_findings(
        findings,
        tenant_id="acme",
        workspace="prod",
        run_id="run-3",
    )

    resource_keys = {item.resource_key for item in nodes}
    edge_types = {item.edge_type for item in edges}

    assert "aws:123456789012:eu-west-3:vpc:nat_gateway:nat-12345678" in resource_keys
    assert "aws:123456789012:eu-west-3:vpc:subnet:subnet-12345678" in resource_keys
    assert "aws:123456789012:eu-west-3:vpc:subnet:subnet-87654321" in resource_keys
    assert "aws:123456789012:eu-west-3:vpc:vpc:vpc-12345678" in resource_keys
    assert (
        "aws:123456789012:eu-west-3:elbv2:load_balancer:"
        "arn:aws:elasticloadbalancing:eu-west-3:123456789012:loadbalancer/app/demo/50dc6c495c0c9188"
    ) in resource_keys
    assert (
        "aws:123456789012:eu-west-3:elbv2:target_group:"
        "arn:aws:elasticloadbalancing:eu-west-3:123456789012:targetgroup/demo/6d0ecf831eec9f09"
    ) in resource_keys
    assert (
        "aws:123456789012:eu-west-3:elbv2:target_group:"
        "arn:aws:elasticloadbalancing:eu-west-3:123456789012:targetgroup/demo2/6d0ecf831eec9f10"
    ) in resource_keys
    assert "attached_to" in edge_types
    assert "member_of" in edge_types
    assert "routes_to" in edge_types
    assert "routes_via" in edge_types


def test_build_graph_from_findings_normalizes_amazonec2_service_names() -> None:
    """Graph builder should normalize AWS service aliases into stable graph keys."""
    findings = [
        {
            "title": "Unattached volume",
            "check_id": "aws.ec2.ebs.unattached",
            "severity": "medium",
            "scope": {
                "account_id": "123456789012",
                "region": "eu-west-3",
                "service": "AmazonEC2",
                "resource_type": "ebs_volume",
                "resource_id": "vol-abcdef12",
                "resource_arn": "",
            },
            "payload": {"dimensions": {}},
        }
    ]

    nodes, _edges = build_graph_from_findings(
        findings,
        tenant_id="acme",
        workspace="prod",
        run_id="run-4",
    )

    assert [item.resource_key for item in nodes] == [
        "aws:123456789012:eu-west-3:ec2:volume:vol-abcdef12"
    ]
