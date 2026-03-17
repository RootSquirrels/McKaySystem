"""Shared types and helpers for resource graph builders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

GRAPH_DIRNAME = "graph"
GRAPH_NODES_FILENAME = "nodes.jsonl"
GRAPH_EDGES_FILENAME = "edges.jsonl"


@dataclass(frozen=True)
class ResourceGraphNode:
    """One deterministic graph node for a resource discovered in a run."""

    tenant_id: str
    workspace: str
    run_id: str
    resource_key: str
    provider: str
    service: str
    resource_type: str
    account_id: str
    region: str
    resource_id: str | None = None
    resource_arn: str | None = None
    resource_name: str | None = None
    parent_resource_key: str | None = None
    state: str | None = None
    tags_json: dict[str, str] | None = None
    attributes_json: dict[str, Any] | None = None
    owner_hint: str | None = None
    is_deleted: bool = False
    first_seen_in_run: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the node to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResourceGraphNode":
        """Hydrate one node from a dict payload."""
        tags_json = payload.get("tags_json")
        attributes_json = payload.get("attributes_json")
        return cls(
            tenant_id=str(payload.get("tenant_id") or "").strip(),
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            resource_key=str(payload.get("resource_key") or "").strip(),
            provider=str(payload.get("provider") or "").strip() or "aws",
            service=str(payload.get("service") or "").strip() or "unknown",
            resource_type=str(payload.get("resource_type") or "").strip() or "resource",
            account_id=str(payload.get("account_id") or "").strip(),
            region=str(payload.get("region") or "").strip(),
            resource_id=(str(payload.get("resource_id") or "").strip() or None),
            resource_arn=(str(payload.get("resource_arn") or "").strip() or None),
            resource_name=(str(payload.get("resource_name") or "").strip() or None),
            parent_resource_key=(str(payload.get("parent_resource_key") or "").strip() or None),
            state=(str(payload.get("state") or "").strip() or None),
            tags_json=(tags_json if isinstance(tags_json, dict) else None),
            attributes_json=(attributes_json if isinstance(attributes_json, dict) else None),
            owner_hint=(str(payload.get("owner_hint") or "").strip() or None),
            is_deleted=bool(payload.get("is_deleted", False)),
            first_seen_in_run=(str(payload.get("first_seen_in_run") or "").strip() or None),
        )


@dataclass(frozen=True)
class ResourceGraphEdge:
    """One deterministic graph edge between two resources in a run."""

    tenant_id: str
    workspace: str
    run_id: str
    edge_key: str
    from_resource_key: str
    to_resource_key: str
    edge_type: str
    service: str
    account_id: str
    region: str
    directionality: str = "directed"
    confidence: str = "high"
    source_kind: str = "derived"
    attributes_json: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the edge to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResourceGraphEdge":
        """Hydrate one edge from a dict payload."""
        attributes_json = payload.get("attributes_json")
        return cls(
            tenant_id=str(payload.get("tenant_id") or "").strip(),
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            edge_key=str(payload.get("edge_key") or "").strip(),
            from_resource_key=str(payload.get("from_resource_key") or "").strip(),
            to_resource_key=str(payload.get("to_resource_key") or "").strip(),
            edge_type=str(payload.get("edge_type") or "").strip(),
            service=str(payload.get("service") or "").strip() or "unknown",
            account_id=str(payload.get("account_id") or "").strip(),
            region=str(payload.get("region") or "").strip(),
            directionality=str(payload.get("directionality") or "").strip() or "directed",
            confidence=str(payload.get("confidence") or "").strip() or "high",
            source_kind=str(payload.get("source_kind") or "").strip() or "derived",
            attributes_json=(attributes_json if isinstance(attributes_json, dict) else None),
        )


def graph_dir(base_dir: str | Path) -> Path:
    """Return the canonical graph artifact directory for one run."""
    return Path(base_dir) / GRAPH_DIRNAME


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_graph_bundle(
    base_dir: str | Path,
    *,
    nodes: list[ResourceGraphNode],
    edges: list[ResourceGraphEdge],
) -> Path:
    """Write graph nodes and edges under the run artifact directory."""
    out_dir = graph_dir(base_dir)
    _write_jsonl(out_dir / GRAPH_NODES_FILENAME, [item.to_dict() for item in nodes])
    _write_jsonl(out_dir / GRAPH_EDGES_FILENAME, [item.to_dict() for item in edges])
    return out_dir


def load_graph_bundle(base_dir: str | Path) -> tuple[list[ResourceGraphNode], list[ResourceGraphEdge]]:
    """Load graph nodes and edges from one graph artifact directory."""
    base = Path(base_dir)
    nodes = [ResourceGraphNode.from_dict(item) for item in _load_jsonl(base / GRAPH_NODES_FILENAME)]
    edges = [ResourceGraphEdge.from_dict(item) for item in _load_jsonl(base / GRAPH_EDGES_FILENAME)]
    return nodes, edges


def as_record(value: Any) -> dict[str, Any] | None:
    """Return a dict view of *value* when it is a mapping."""
    if isinstance(value, dict):
        return dict(value)
    return None


def non_empty_text(value: Any) -> str | None:
    """Normalize optional text values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def infer_resource_kind(resource_id: str | None, resource_arn: str | None) -> tuple[str, str]:
    """Infer a graph resource type and service from common AWS ids."""
    arn_text = non_empty_text(resource_arn) or ""
    resource_text = non_empty_text(resource_id) or ""
    if arn_text:
        lowered = arn_text.lower()
        if ":elasticloadbalancing:" in lowered:
            return "load_balancer", "elbv2"
        if ":lambda:" in lowered:
            return "function", "lambda"
        if ":rds:" in lowered:
            return "db_instance", "rds"
        if ":s3:::" in lowered:
            return "bucket", "s3"
    if resource_text.startswith("i-"):
        return "instance", "ec2"
    if resource_text.startswith("vol-"):
        return "volume", "ec2"
    if resource_text.startswith("vpc-"):
        return "vpc", "vpc"
    if resource_text.startswith("subnet-"):
        return "subnet", "vpc"
    if resource_text.startswith("nat-"):
        return "nat_gateway", "vpc"
    if resource_text.startswith("sg-"):
        return "security_group", "ec2"
    if resource_text.startswith("rtb-"):
        return "route_table", "vpc"
    if resource_text.startswith("tg-"):
        return "target_group", "elbv2"
    return "resource", "unknown"


def normalize_resource_type(resource_type: str | None) -> str:
    """Normalize common AWS resource type variants to graph-friendly ids."""
    normalized = (non_empty_text(resource_type) or "resource").lower().replace("-", "_")
    aliases = {
        "ebs_volume": "volume",
        "ebs_snapshot": "snapshot",
        "s3_bucket": "bucket",
        "ec2_instance": "instance",
        "security_group": "security_group",
        "nat_gateway": "nat_gateway",
        "db_instance": "db_instance",
    }
    return aliases.get(normalized, normalized)


def normalize_service(service: str | None, *, resource_type: str | None = None) -> str:
    """Normalize service names to stable graph service ids."""
    normalized = (non_empty_text(service) or "unknown").lower().replace(" ", "")
    normalized_type = normalize_resource_type(resource_type)
    if normalized_type in {"vpc", "subnet", "nat_gateway", "route_table"}:
        return "vpc"
    if normalized_type in {"instance", "volume", "snapshot", "security_group"}:
        return "ec2"
    if normalized_type == "bucket":
        return "s3"
    if normalized_type in {"load_balancer", "target_group"}:
        return "elbv2"
    if normalized_type == "db_instance":
        return "rds"
    if normalized_type == "function":
        return "lambda"

    aliases = {
        "amazonec2": "ec2",
        "ec2": "ec2",
        "vpc": "vpc",
        "amazons3": "s3",
        "s3": "s3",
        "elasticloadbalancingv2": "elbv2",
        "elbv2": "elbv2",
        "rds": "rds",
        "awslambda": "lambda",
        "lambda": "lambda",
    }
    return aliases.get(normalized, normalized or "unknown")


def resource_key(
    *,
    account_id: str,
    region: str,
    service: str,
    resource_type: str,
    native_id: str,
) -> str:
    """Build a deterministic graph resource key."""
    return f"aws:{account_id}:{region}:{service}:{resource_type}:{native_id}"


def edge_key(from_resource_key: str, edge_type: str, to_resource_key: str) -> str:
    """Build a deterministic graph edge key."""
    raw = f"{from_resource_key}|{edge_type}|{to_resource_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class GraphBuildState:
    """Mutable graph build state shared across service builders."""

    tenant_id: str
    workspace: str
    run_id: str
    nodes: dict[str, ResourceGraphNode]
    edges: dict[str, ResourceGraphEdge]

    def ensure_node(
        self,
        *,
        account_id: str,
        region: str,
        service: str,
        resource_type: str,
        native_id: str,
        resource_arn: str | None = None,
        resource_name: str | None = None,
        attributes_json: dict[str, Any] | None = None,
        parent_resource_key: str | None = None,
    ) -> str:
        """Upsert one deterministic graph node and return its resource key."""
        normalized_type = normalize_resource_type(resource_type)
        normalized_service = normalize_service(service, resource_type=normalized_type)
        key = resource_key(
            account_id=account_id,
            region=region,
            service=normalized_service,
            resource_type=normalized_type,
            native_id=native_id,
        )
        existing = self.nodes.get(key)
        if existing is None:
            self.nodes[key] = ResourceGraphNode(
                tenant_id=self.tenant_id,
                workspace=self.workspace,
                run_id=self.run_id,
                resource_key=key,
                provider="aws",
                service=normalized_service,
                resource_type=normalized_type,
                account_id=account_id,
                region=region,
                resource_id=native_id,
                resource_arn=resource_arn,
                resource_name=resource_name,
                parent_resource_key=parent_resource_key,
                attributes_json=attributes_json,
                first_seen_in_run=self.run_id,
            )
        else:
            self.nodes[key] = replace(
                existing,
                resource_arn=existing.resource_arn or resource_arn,
                resource_name=existing.resource_name or resource_name,
                parent_resource_key=existing.parent_resource_key or parent_resource_key,
                attributes_json=existing.attributes_json or attributes_json,
            )
        return key

    def ensure_edge(
        self,
        *,
        from_resource_key: str,
        to_resource_key: str,
        edge_type: str,
        service: str,
        account_id: str,
        region: str,
        attributes_json: dict[str, Any] | None = None,
    ) -> None:
        """Upsert one deterministic graph edge."""
        key = edge_key(from_resource_key, edge_type, to_resource_key)
        self.edges[key] = ResourceGraphEdge(
            tenant_id=self.tenant_id,
            workspace=self.workspace,
            run_id=self.run_id,
            edge_key=key,
            from_resource_key=from_resource_key,
            to_resource_key=to_resource_key,
            edge_type=edge_type,
            service=service,
            account_id=account_id,
            region=region,
            source_kind="derived",
            attributes_json=attributes_json,
        )

    def related_node_for_id(
        self,
        *,
        related_id: str,
        account_id: str,
        region: str,
    ) -> tuple[str, str, str]:
        """Ensure a related node from a native AWS id and return key/type/service."""
        related_type, related_service = infer_resource_kind(related_id, None)
        related_key = self.ensure_node(
            account_id=account_id,
            region=region,
            service=related_service,
            resource_type=related_type,
            native_id=related_id,
        )
        return related_key, related_type, related_service
