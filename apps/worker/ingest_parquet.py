"""
apps.worker.ingest_parquet

Ingest findings from Parquet datasets into Postgres using run_manifest.json
as the single source of truth for tenant/workspace/run and dataset paths.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

from apps.backend.db import db_conn, execute, execute_many, fetch_one
from apps.worker.coverage_model import CoverageIssue, CoverageResult, load_coverage_bundle
from apps.worker.resource_graph_model import ResourceGraphEdge, ResourceGraphNode, load_graph_bundle
from apps.backend.run_state import (
    STATE_READY,
    acquire_run_lock,
    append_run_event,
    begin_run_running,
    default_owner,
    release_run_lock,
    transition_run_to_failed,
    transition_run_to_ready,
)
from infra.config import get_settings
from pipeline.run_manifest import find_manifest, load_manifest
from services.remediation.impact import refresh_scope_action_impacts
from version import SCHEMA_VERSION

logger = logging.getLogger(__name__)
_IMPACT_REFRESH_LIMIT = 500


def _lock_ttl_seconds() -> int:
    """Lock TTL for run-scoped ingestion lock."""
    return int(get_settings(reload=True).worker.run_lock_ttl_seconds)


def _parse_dt(value: Any) -> datetime | None:
    """Parse a datetime from common inputs, returning UTC-aware."""
    if isinstance(value, datetime):
        dt = value
    else:
        v = (str(value or "")).strip()
        if not v:
            return None
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _manifest_run_ts(manifest_run_ts: str) -> datetime:
    """Parse and validate run_ts from manifest (required, deterministic)."""
    run_ts = _parse_dt(manifest_run_ts)
    if run_ts is None:
        raise SystemExit(f"Invalid run_ts in manifest: {manifest_run_ts!r}")
    return run_ts


def _json_default(obj: Any) -> Any:
    """JSON serializer for datetime/Decimal values."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)


def _to_float(v: Any) -> float | None:
    """Best-effort numeric parsing for savings/cost fields."""
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, dict):
        for k in ("amount", "value", "usd", "eur"):
            if k in v:
                return _to_float(v.get(k))
        return None
    try:
        s = str(v).strip()
        if not s:
            return None
        s = re.sub(r"[^0-9,\\.-]", "", s)
        if s.count(",") == 1 and s.count(".") == 0:
            s = s.replace(",", ".")
        if s.count(",") >= 1 and s.count(".") >= 1:
            s = s.replace(",", "")
        return float(s)
    except (TypeError, ValueError, OverflowError):
        return None


_CATEGORY_BY_PREFIX: list[tuple[str, str]] = [
    ("aws.cloudwatch.", "cost"),
    ("aws.ec2.", "cost"),
    ("aws.ebs.", "cost"),
    ("aws.elb", "cost"),
    ("aws.rds.", "cost"),
    ("aws.s3.", "cost"),
    ("aws.vpc.", "cost"),
    ("aws.fsx.", "cost"),
    ("aws.backup.", "reliability"),
    ("aws.iam.", "security"),
]


def _derive_category(check_id: str | None) -> str:
    """Infer a coarse category from check_id prefix."""
    if not check_id:
        return "other"
    for prefix, cat in _CATEGORY_BY_PREFIX:
        if check_id.startswith(prefix):
            return cat
    return "other"


_ID_PATTERNS = [
    r"\\barn:[^\\s]+",
    r"\\bi-[0-9a-f]{8,}\\b",
    r"\\bvol-[0-9a-f]{8,}\\b",
    r"\\bsg-[0-9a-f]{8,}\\b",
    r"\\bsubnet-[0-9a-f]{8,}\\b",
    r"\\bvpc-[0-9a-f]{8,}\\b",
    r"\\b[a-z0-9-]{1,63}\\.amazonaws\\.com\\b",
]


def _normalize_title(title: str | None) -> str:
    """Normalize titles for grouping (mask IDs and digits)."""
    t = (title or "").strip().lower()
    if not t:
        return ""
    for pat in _ID_PATTERNS:
        t = re.sub(pat, "<id>", t)
    t = re.sub(r"\\d+", "<n>", t)
    t = re.sub(r"\\s+", " ", t)
    return t.strip()


def _derive_group_key(check_id: str | None, category: str, title: str | None) -> str | None:
    """Build a stable group key from check_id/category/title."""
    base = f"{(check_id or '').strip()}|{category}|{_normalize_title(title)}".strip("|")
    if not base:
        return None
    import hashlib

    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _scope_get(scope: Any, key: str) -> str | None:
    """Safe access to a scope mapping field."""
    if isinstance(scope, Mapping):
        v = scope.get(key)
        return str(v).strip() if v is not None else None
    return None


def _guess_fields_from_record(
    rec: Mapping[str, Any],
) -> tuple[
    str | None, str | None, str | None, str | None,
    float | None, str | None, str | None,
    str, str | None,
]:
    """Extract DB fields from a Parquet record with best-effort fallbacks."""
    check_id = rec.get("check_id")
    if check_id is not None:
        check_id = str(check_id).strip() or None

    scope = rec.get("scope") or {}
    service = _scope_get(scope, "service") or (str(rec.get("service") or "").strip() or None)

    severity = None
    sev = rec.get("severity")
    if isinstance(sev, Mapping):
        severity = str(sev.get("level") or "").strip() or None
    elif sev is not None:
        severity = str(sev).strip() or None

    title = str(rec.get("title") or "").strip() or None

    category = str(rec.get("category") or "").strip() or ""
    if not category:
        category = _derive_category(check_id)

    group_key = str(rec.get("group_key") or rec.get("groupKey") or "").strip() or None
    if not group_key:
        group_key = _derive_group_key(check_id, category, title)

    estimated = rec.get("estimated") if isinstance(rec.get("estimated"), Mapping) else {}
    savings = estimated.get("monthly_savings") if isinstance(estimated, Mapping) else None
    savings_f = _to_float(savings)

    region = _scope_get(scope, "region") or (str(rec.get("region") or "").strip() or None)
    account_id = _scope_get(scope, "account_id") or (str(rec.get("account_id") or "").strip() or None)

    return check_id, service, severity, title, savings_f, region, account_id, category, group_key


def _glob_has_files(path: str | Path) -> bool:
    """Return True if the path contains any parquet files."""
    base = Path(path)
    if not base.exists():
        return False
    return bool(list(base.glob("**/*.parquet")))


def _list_parquet_files(path: str | Path) -> list[Path]:
    """List parquet files under a dataset directory."""
    base = Path(path)
    if not base.exists():
        return []
    return [p for p in base.rglob("*.parquet") if p.is_file()]


def _as_copy_value(value: Any) -> str:
    """Normalize a value for CSV COPY."""
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    return str(value)


def _copy_rows(cur, table: str, columns: Sequence[str], rows: list[Sequence[Any]]) -> int:
    """Bulk copy rows into a table using CSV COPY."""
    if not rows:
        return 0
    buf = io.StringIO()
    writer = csv.writer(
        buf,
        delimiter="\t",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )
    for row in rows:
        writer.writerow([_as_copy_value(v) for v in row])
    buf.seek(0)

    cols_sql = ", ".join(columns)
    # NOTE: table/columns are internal constants; do not pass user input here.
    sql = f"COPY {table} ({cols_sql}) FROM STDIN WITH (FORMAT CSV, DELIMITER E'\\t', NULL '\\\\N')"
    cur.copy_expert(sql, buf)
    return len(rows)


def _selected_dataset_paths(manifest) -> tuple[list[str], str]:
    """Resolve dataset paths to ingest.

    Rules:
    - If enriched exists, ingest enriched only (already includes merged findings).
    - Otherwise ingest raw and correlated together when available.
    """
    if manifest.out_enriched and _glob_has_files(manifest.out_enriched):
        return [manifest.out_enriched], "enriched"

    selected: list[str] = []
    labels: list[str] = []
    if manifest.out_raw and _glob_has_files(manifest.out_raw):
        selected.append(manifest.out_raw)
        labels.append("raw")
    if manifest.out_correlated and _glob_has_files(manifest.out_correlated):
        selected.append(manifest.out_correlated)
        labels.append("correlated")

    if selected:
        return selected, "+".join(labels)

    # Fall back to configured paths for a clearer error message upstream.
    fallback: list[str] = []
    if manifest.out_enriched:
        fallback.append(manifest.out_enriched)
    if manifest.out_raw:
        fallback.append(manifest.out_raw)
    if manifest.out_correlated:
        fallback.append(manifest.out_correlated)
    return fallback, "none"


def _list_parquet_files_for_paths(paths: Sequence[str]) -> list[Path]:
    """List parquet files across multiple dataset roots."""
    files: list[Path] = []
    for path in paths:
        files.extend(_list_parquet_files(path))
    # Deterministic and de-duplicated
    return sorted({p.resolve() for p in files})


@dataclass(frozen=True)
class DbApi:
    execute: Callable[[str, Sequence[Any] | None], None]
    execute_many: Callable[[str, list[Sequence[Any]]], None]
    fetch_one: Callable[[str, Sequence[Any] | None], tuple[Any, ...] | None]


def _default_db_api() -> DbApi:
    """Return the default DB API backed by apps.backend.db helpers."""
    return DbApi(execute=execute, execute_many=execute_many, fetch_one=fetch_one)


def _ensure_db_schema_current() -> None:
    """Fail fast if the database schema is behind local migrations."""
    from apps.backend.db_migrate import ensure_schema_current

    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    ensure_schema_current(migrations_dir=migrations_dir)


@dataclass
class IngestStats:
    dataset_used: str
    dataset_dir: str
    raw_present: bool
    correlated_present: bool
    enriched_present: bool
    presence_rows: int
    latest_rows: int
    coverage_rows: int = 0
    coverage_issue_rows: int = 0
    graph_node_rows: int = 0
    graph_edge_rows: int = 0


@dataclass(frozen=True)
class CoverageSummary:
    """Computed run coverage summary persisted to Postgres."""

    targets_total: int
    assessed_total: int
    assessed_with_findings: int
    assessed_no_issue: int
    assessment_failed: int
    skipped_total: int
    not_assessed_total: int
    permission_gap_count: int
    coverage_pct: float
    coverage_status: str
    confidence: str


_AGG_DELETE_SQL = """
DELETE FROM finding_aggregates_current
WHERE tenant_id=%s AND workspace=%s
"""


_AGG_INSERT_SQL = """
INSERT INTO finding_aggregates_current
  (tenant_id, workspace, dimension, key, finding_count, total_savings, refreshed_at)
SELECT
  tenant_id,
  workspace,
  dimension,
  key,
  finding_count,
  total_savings,
  now()
FROM (
  SELECT
    tenant_id,
    workspace,
    'effective_state'::text AS dimension,
    COALESCE(effective_state, 'open') AS key,
    COUNT(*)::bigint AS finding_count,
    COALESCE(SUM(estimated_monthly_savings), 0)::double precision AS total_savings
  FROM finding_current
  WHERE tenant_id=%s AND workspace=%s
  GROUP BY tenant_id, workspace, COALESCE(effective_state, 'open')

  UNION ALL

  SELECT
    tenant_id,
    workspace,
    'severity'::text AS dimension,
    COALESCE(severity, 'unknown') AS key,
    COUNT(*)::bigint AS finding_count,
    COALESCE(SUM(estimated_monthly_savings), 0)::double precision AS total_savings
  FROM finding_current
  WHERE tenant_id=%s AND workspace=%s
  GROUP BY tenant_id, workspace, COALESCE(severity, 'unknown')

  UNION ALL

  SELECT
    tenant_id,
    workspace,
    'service'::text AS dimension,
    COALESCE(service, 'unknown') AS key,
    COUNT(*)::bigint AS finding_count,
    COALESCE(SUM(estimated_monthly_savings), 0)::double precision AS total_savings
  FROM finding_current
  WHERE tenant_id=%s AND workspace=%s
  GROUP BY tenant_id, workspace, COALESCE(service, 'unknown')

  UNION ALL

  SELECT
    tenant_id,
    workspace,
    'category'::text AS dimension,
    COALESCE(category, 'other') AS key,
    COUNT(*)::bigint AS finding_count,
    COALESCE(SUM(estimated_monthly_savings), 0)::double precision AS total_savings
  FROM finding_current
  WHERE tenant_id=%s AND workspace=%s
  GROUP BY tenant_id, workspace, COALESCE(category, 'other')
) agg
ON CONFLICT (tenant_id, workspace, dimension, key) DO UPDATE SET
  finding_count = EXCLUDED.finding_count,
  total_savings = EXCLUDED.total_savings,
  refreshed_at = EXCLUDED.refreshed_at
"""


def _aggregate_params(tenant_id: str, workspace: str) -> tuple[str, str, str, str, str, str, str, str]:
    """Return reusable parameter tuple for aggregate refresh SQL."""
    return (
        tenant_id,
        workspace,
        tenant_id,
        workspace,
        tenant_id,
        workspace,
        tenant_id,
        workspace,
    )


def _refresh_aggregates_with_api(api: DbApi, *, tenant_id: str, workspace: str) -> None:
    """Refresh aggregate read-model rows for one tenant/workspace."""
    api.execute(_AGG_DELETE_SQL, (tenant_id, workspace))
    api.execute(_AGG_INSERT_SQL, _aggregate_params(tenant_id, workspace))


def _refresh_aggregates_with_cursor(cur, *, tenant_id: str, workspace: str) -> None:
    """Refresh aggregate read-model rows inside an existing DB transaction."""
    cur.execute(_AGG_DELETE_SQL, (tenant_id, workspace))
    cur.execute(_AGG_INSERT_SQL, _aggregate_params(tenant_id, workspace))


def _count_from_row(row: tuple[Any, ...] | None, *, label: str) -> int:
    """Read an integer count from a single-row query result."""
    if row is None:
        raise RuntimeError(f"Invariant query returned no row: {label}")
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invariant query returned non-numeric count: {label} row={row!r}") from exc


def _assert_post_ingest_invariants(
    *,
    count_query: Callable[[str, Sequence[Any], str], int],
    tenant_id: str,
    workspace: str,
    run_id: str,
    expected_presence: int,
    expected_latest: int,
) -> None:
    """Validate core ingest invariants before marking a run ready."""
    scope_run_params = (tenant_id, workspace, run_id)

    run_rows = count_query(
        "SELECT COUNT(*) FROM runs WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        scope_run_params,
        "runs row count",
    )
    presence_count = count_query(
        "SELECT COUNT(*) FROM finding_presence WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        scope_run_params,
        "presence count",
    )
    latest_count = count_query(
        "SELECT COUNT(*) FROM finding_latest WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        scope_run_params,
        "latest count",
    )
    presence_distinct = count_query(
        "SELECT COUNT(DISTINCT fingerprint) FROM finding_presence WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        scope_run_params,
        "presence distinct fingerprints",
    )
    latest_distinct = count_query(
        "SELECT COUNT(DISTINCT fingerprint) FROM finding_latest WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        scope_run_params,
        "latest distinct fingerprints",
    )
    missing_latest = count_query(
        """
        SELECT COUNT(*)
        FROM finding_presence fp
        LEFT JOIN finding_latest fl
          ON fl.tenant_id = fp.tenant_id
         AND fl.workspace = fp.workspace
         AND fl.fingerprint = fp.fingerprint
         AND fl.run_id = fp.run_id
        WHERE fp.tenant_id=%s AND fp.workspace=%s AND fp.run_id=%s
          AND fl.fingerprint IS NULL
        """,
        scope_run_params,
        "presence rows missing in latest",
    )

    errors: list[str] = []
    if run_rows != 1:
        errors.append(f"runs row count expected=1 actual={run_rows}")
    if presence_count != int(expected_presence):
        errors.append(f"presence rows expected={expected_presence} actual={presence_count}")
    if latest_count != int(expected_latest):
        errors.append(f"latest rows expected={expected_latest} actual={latest_count}")
    if presence_count != presence_distinct:
        errors.append(
            f"presence duplicate fingerprints count={presence_count} distinct={presence_distinct}"
        )
    if latest_count != latest_distinct:
        errors.append(f"latest duplicate fingerprints count={latest_count} distinct={latest_distinct}")
    if missing_latest != 0:
        errors.append(f"presence rows missing in latest={missing_latest}")

    if errors:
        raise RuntimeError("Post-ingest invariant failed: " + "; ".join(errors))

    logger.info(
        "Post-ingest invariants passed for %s/%s/%s (presence=%s latest=%s)",
        tenant_id,
        workspace,
        run_id,
        presence_count,
        latest_count,
    )


def _assert_post_ingest_invariants_with_api(
    api: DbApi,
    *,
    tenant_id: str,
    workspace: str,
    run_id: str,
    expected_presence: int,
    expected_latest: int,
) -> None:
    """Run post-ingest invariants using the DbApi abstraction."""

    def _count(sql: str, params: Sequence[Any], label: str) -> int:
        return _count_from_row(api.fetch_one(sql, params), label=label)

    _assert_post_ingest_invariants(
        count_query=_count,
        tenant_id=tenant_id,
        workspace=workspace,
        run_id=run_id,
        expected_presence=expected_presence,
        expected_latest=expected_latest,
    )


def _assert_post_ingest_invariants_with_cursor(
    cur: Any,
    *,
    tenant_id: str,
    workspace: str,
    run_id: str,
    expected_presence: int,
    expected_latest: int,
) -> None:
    """Run post-ingest invariants inside an existing DB transaction."""

    def _count(sql: str, params: Sequence[Any], label: str) -> int:
        cur.execute(sql, params)
        return _count_from_row(cur.fetchone(), label=label)

    _assert_post_ingest_invariants(
        count_query=_count,
        tenant_id=tenant_id,
        workspace=workspace,
        run_id=run_id,
        expected_presence=expected_presence,
        expected_latest=expected_latest,
    )


def _load_run_coverage(manifest: Any) -> tuple[list[CoverageResult], list[CoverageIssue]]:
    """Load coverage artifacts when available for the manifest."""
    coverage_dir = str(getattr(manifest, "coverage_dir", "") or "").strip()
    if not coverage_dir:
        return [], []
    return load_coverage_bundle(coverage_dir)


def _load_run_graph(manifest: Any) -> tuple[list[ResourceGraphNode], list[ResourceGraphEdge]]:
    """Load graph artifacts when available for the manifest."""
    graph_dir = str(getattr(manifest, "graph_dir", "") or "").strip()
    if not graph_dir:
        return [], []
    return load_graph_bundle(graph_dir)


def _coverage_summary(results: Sequence[CoverageResult]) -> CoverageSummary:
    """Compute deterministic summary counters from coverage rows."""
    targets_total = len(results)
    assessed_with_findings = sum(1 for item in results if item.status == "assessed_with_findings")
    assessed_no_issue = sum(1 for item in results if item.status == "assessed_no_issue")
    assessment_failed = sum(1 for item in results if item.status == "assessment_failed")
    skipped_total = sum(1 for item in results if item.status == "skipped")
    not_assessed_total = sum(1 for item in results if item.status == "not_assessed")
    assessed_total = assessed_with_findings + assessed_no_issue
    permission_gap_count = sum(max(0, int(item.permission_gap_count or 0)) for item in results)
    coverage_pct = round((assessed_total / targets_total) * 100.0, 2) if targets_total else 0.0

    if targets_total == 0 or assessment_failed == targets_total:
        coverage_status = "failed"
    elif assessment_failed > 0 or coverage_pct < 80.0:
        coverage_status = "degraded"
    elif skipped_total > 0 or not_assessed_total > 0:
        coverage_status = "partial"
    else:
        coverage_status = "healthy"

    if assessed_total == 0:
        confidence = "none"
    elif assessment_failed > 0 or permission_gap_count > 0:
        confidence = "low"
    elif skipped_total > 0 or not_assessed_total > 0:
        confidence = "medium"
    else:
        confidence = "high"

    return CoverageSummary(
        targets_total=targets_total,
        assessed_total=assessed_total,
        assessed_with_findings=assessed_with_findings,
        assessed_no_issue=assessed_no_issue,
        assessment_failed=assessment_failed,
        skipped_total=skipped_total,
        not_assessed_total=not_assessed_total,
        permission_gap_count=permission_gap_count,
        coverage_pct=coverage_pct,
        coverage_status=coverage_status,
        confidence=confidence,
    )


def _coverage_result_rows(
    results: Sequence[CoverageResult],
) -> list[tuple[Any, ...]]:
    """Convert coverage results to DB insert rows."""
    return [
        (
            item.tenant_id,
            item.workspace,
            item.run_id,
            item.account_id,
            item.region,
            item.service,
            item.checker_id,
            item.checker_scope,
            item.status,
            int(item.findings_count),
            item.duration_ms,
            item.confidence,
            item.completeness_pct,
            int(item.permission_gap_count),
            item.error_class,
            item.error_code,
            item.error_message,
            item.skip_reason,
            _parse_dt(item.started_at),
            _parse_dt(item.finished_at),
        )
        for item in results
    ]


def _coverage_issue_rows(
    issues: Sequence[CoverageIssue],
) -> list[tuple[Any, ...]]:
    """Convert coverage issues to DB insert rows."""
    return [
        (
            item.tenant_id,
            item.workspace,
            item.run_id,
            item.account_id,
            item.region,
            item.service,
            item.checker_id,
            item.issue_type,
            item.operation,
            item.error_code,
            item.message,
            bool(item.is_retryable),
            item.severity,
            json.dumps(item.payload, ensure_ascii=False, separators=(",", ":")) if item.payload else None,
        )
        for item in issues
    ]


def _graph_node_rows(
    nodes: Sequence[ResourceGraphNode],
) -> list[tuple[Any, ...]]:
    """Convert graph nodes to DB insert rows."""
    return [
        (
            item.tenant_id,
            item.workspace,
            item.run_id,
            item.resource_key,
            item.provider,
            item.service,
            item.resource_type,
            item.account_id,
            item.region,
            item.resource_id,
            item.resource_arn,
            item.resource_name,
            item.parent_resource_key,
            item.state,
            json.dumps(item.tags_json or {}, ensure_ascii=False, default=_json_default),
            json.dumps(item.attributes_json or {}, ensure_ascii=False, default=_json_default),
            item.owner_hint,
            item.is_deleted,
            item.first_seen_in_run,
        )
        for item in nodes
    ]


def _graph_edge_rows(
    edges: Sequence[ResourceGraphEdge],
) -> list[tuple[Any, ...]]:
    """Convert graph edges to DB insert rows."""
    return [
        (
            item.tenant_id,
            item.workspace,
            item.run_id,
            item.edge_key,
            item.from_resource_key,
            item.to_resource_key,
            item.edge_type,
            item.service,
            item.account_id,
            item.region,
            item.directionality,
            item.confidence,
            item.source_kind,
            json.dumps(item.attributes_json or {}, ensure_ascii=False, default=_json_default),
        )
        for item in edges
    ]


def _graph_node_current_row(
    row: Sequence[Any],
    *,
    latest_run_id: str,
    latest_run_ts: datetime,
) -> tuple[Any, ...]:
    """Convert one run-scoped node row to the current-table shape."""
    return (
        row[0],
        row[1],
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
        row[12],
        row[13],
        row[14],
        row[15],
        row[16],
        row[17],
        latest_run_id,
        latest_run_ts,
    )


def _graph_edge_current_row(
    row: Sequence[Any],
    *,
    latest_run_id: str,
    latest_run_ts: datetime,
) -> tuple[Any, ...]:
    """Convert one run-scoped edge row to the current-table shape."""
    return (
        row[0],
        row[1],
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
        row[12],
        row[13],
        latest_run_id,
        latest_run_ts,
    )


def _persist_graph_with_api(
    api: DbApi,
    *,
    manifest: Any,
    nodes: Sequence[ResourceGraphNode],
    edges: Sequence[ResourceGraphEdge],
) -> None:
    """Persist graph nodes and edges using the DbApi abstraction."""
    api.execute(
        "DELETE FROM resource_graph_edges_run WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    api.execute(
        "DELETE FROM resource_graph_nodes_run WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    node_rows = _graph_node_rows(nodes)
    if node_rows:
        api.execute_many(
            """
            INSERT INTO resource_graph_nodes_run
              (tenant_id, workspace, run_id, resource_key, provider, service, resource_type,
               account_id, region, resource_id, resource_arn, resource_name, parent_resource_key,
               state, tags_json, attributes_json, owner_hint, is_deleted, first_seen_in_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            node_rows,
        )
    edge_rows = _graph_edge_rows(edges)
    if edge_rows:
        api.execute_many(
            """
            INSERT INTO resource_graph_edges_run
              (tenant_id, workspace, run_id, edge_key, from_resource_key, to_resource_key,
               edge_type, service, account_id, region, directionality, confidence,
               source_kind, attributes_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            edge_rows,
        )

    run_ts = _manifest_run_ts(manifest.run_ts)
    api.execute(
        "DELETE FROM resource_graph_nodes_current WHERE tenant_id=%s AND workspace=%s AND latest_run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    api.execute(
        "DELETE FROM resource_graph_edges_current WHERE tenant_id=%s AND workspace=%s AND latest_run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    for row in node_rows:
        current_row = _graph_node_current_row(
            row,
            latest_run_id=manifest.run_id,
            latest_run_ts=run_ts,
        )
        api.execute(
            """
            INSERT INTO resource_graph_nodes_current
              (tenant_id, workspace, resource_key, provider, service, resource_type, account_id,
               region, resource_id, resource_arn, resource_name, parent_resource_key, state,
               tags_json, attributes_json, owner_hint, is_deleted, latest_run_id, latest_run_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, resource_key) DO UPDATE SET
              provider=EXCLUDED.provider,
              service=EXCLUDED.service,
              resource_type=EXCLUDED.resource_type,
              account_id=EXCLUDED.account_id,
              region=EXCLUDED.region,
              resource_id=EXCLUDED.resource_id,
              resource_arn=EXCLUDED.resource_arn,
              resource_name=EXCLUDED.resource_name,
              parent_resource_key=EXCLUDED.parent_resource_key,
              state=EXCLUDED.state,
              tags_json=EXCLUDED.tags_json,
              attributes_json=EXCLUDED.attributes_json,
              owner_hint=EXCLUDED.owner_hint,
              is_deleted=EXCLUDED.is_deleted,
              latest_run_id=EXCLUDED.latest_run_id,
              latest_run_ts=EXCLUDED.latest_run_ts
            WHERE resource_graph_nodes_current.latest_run_ts <= EXCLUDED.latest_run_ts
            """,
            current_row,
        )
    for row in edge_rows:
        current_row = _graph_edge_current_row(
            row,
            latest_run_id=manifest.run_id,
            latest_run_ts=run_ts,
        )
        api.execute(
            """
            INSERT INTO resource_graph_edges_current
              (tenant_id, workspace, edge_key, from_resource_key, to_resource_key, edge_type,
               service, account_id, region, directionality, confidence, source_kind,
               attributes_json, latest_run_id, latest_run_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, edge_key) DO UPDATE SET
              from_resource_key=EXCLUDED.from_resource_key,
              to_resource_key=EXCLUDED.to_resource_key,
              edge_type=EXCLUDED.edge_type,
              service=EXCLUDED.service,
              account_id=EXCLUDED.account_id,
              region=EXCLUDED.region,
              directionality=EXCLUDED.directionality,
              confidence=EXCLUDED.confidence,
              source_kind=EXCLUDED.source_kind,
              attributes_json=EXCLUDED.attributes_json,
              latest_run_id=EXCLUDED.latest_run_id,
              latest_run_ts=EXCLUDED.latest_run_ts
            WHERE resource_graph_edges_current.latest_run_ts <= EXCLUDED.latest_run_ts
            """,
            current_row,
        )


def _persist_graph_with_cursor(
    cur: Any,
    *,
    manifest: Any,
    nodes: Sequence[ResourceGraphNode],
    edges: Sequence[ResourceGraphEdge],
) -> None:
    """Persist graph nodes and edges inside an existing transaction."""
    cur.execute(
        "DELETE FROM resource_graph_edges_run WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    cur.execute(
        "DELETE FROM resource_graph_nodes_run WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )

    node_rows = _graph_node_rows(nodes)
    if node_rows:
        cur.executemany(
            """
            INSERT INTO resource_graph_nodes_run
              (tenant_id, workspace, run_id, resource_key, provider, service, resource_type,
               account_id, region, resource_id, resource_arn, resource_name, parent_resource_key,
               state, tags_json, attributes_json, owner_hint, is_deleted, first_seen_in_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            node_rows,
        )

    edge_rows = _graph_edge_rows(edges)
    if edge_rows:
        cur.executemany(
            """
            INSERT INTO resource_graph_edges_run
              (tenant_id, workspace, run_id, edge_key, from_resource_key, to_resource_key,
               edge_type, service, account_id, region, directionality, confidence,
               source_kind, attributes_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            edge_rows,
        )

    run_ts = _manifest_run_ts(manifest.run_ts)
    cur.execute(
        "DELETE FROM resource_graph_nodes_current WHERE tenant_id=%s AND workspace=%s AND latest_run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    cur.execute(
        "DELETE FROM resource_graph_edges_current WHERE tenant_id=%s AND workspace=%s AND latest_run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )

    for row in node_rows:
        current_row = _graph_node_current_row(
            row,
            latest_run_id=manifest.run_id,
            latest_run_ts=run_ts,
        )
        cur.execute(
            """
            INSERT INTO resource_graph_nodes_current
              (tenant_id, workspace, resource_key, provider, service, resource_type, account_id,
               region, resource_id, resource_arn, resource_name, parent_resource_key, state,
               tags_json, attributes_json, owner_hint, is_deleted, latest_run_id, latest_run_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, resource_key) DO UPDATE SET
              provider=EXCLUDED.provider,
              service=EXCLUDED.service,
              resource_type=EXCLUDED.resource_type,
              account_id=EXCLUDED.account_id,
              region=EXCLUDED.region,
              resource_id=EXCLUDED.resource_id,
              resource_arn=EXCLUDED.resource_arn,
              resource_name=EXCLUDED.resource_name,
              parent_resource_key=EXCLUDED.parent_resource_key,
              state=EXCLUDED.state,
              tags_json=EXCLUDED.tags_json,
              attributes_json=EXCLUDED.attributes_json,
              owner_hint=EXCLUDED.owner_hint,
              is_deleted=EXCLUDED.is_deleted,
              latest_run_id=EXCLUDED.latest_run_id,
              latest_run_ts=EXCLUDED.latest_run_ts
            WHERE resource_graph_nodes_current.latest_run_ts <= EXCLUDED.latest_run_ts
            """,
            current_row,
        )

    for row in edge_rows:
        current_row = _graph_edge_current_row(
            row,
            latest_run_id=manifest.run_id,
            latest_run_ts=run_ts,
        )
        cur.execute(
            """
            INSERT INTO resource_graph_edges_current
              (tenant_id, workspace, edge_key, from_resource_key, to_resource_key, edge_type,
               service, account_id, region, directionality, confidence, source_kind,
               attributes_json, latest_run_id, latest_run_ts)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, edge_key) DO UPDATE SET
              from_resource_key=EXCLUDED.from_resource_key,
              to_resource_key=EXCLUDED.to_resource_key,
              edge_type=EXCLUDED.edge_type,
              service=EXCLUDED.service,
              account_id=EXCLUDED.account_id,
              region=EXCLUDED.region,
              directionality=EXCLUDED.directionality,
              confidence=EXCLUDED.confidence,
              source_kind=EXCLUDED.source_kind,
              attributes_json=EXCLUDED.attributes_json,
              latest_run_id=EXCLUDED.latest_run_id,
              latest_run_ts=EXCLUDED.latest_run_ts
            WHERE resource_graph_edges_current.latest_run_ts <= EXCLUDED.latest_run_ts
            """,
            current_row,
        )


def _persist_coverage_with_api(
    api: DbApi,
    *,
    tenant_id: str,
    workspace: str,
    run_id: str,
    results: Sequence[CoverageResult],
    issues: Sequence[CoverageIssue],
) -> CoverageSummary:
    """Persist coverage rows and summary using the DbApi abstraction."""
    summary = _coverage_summary(results)
    api.execute(
        "DELETE FROM run_checker_coverage WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (tenant_id, workspace, run_id),
    )
    api.execute(
        "DELETE FROM run_coverage_issues WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (tenant_id, workspace, run_id),
    )
    api.execute(
        "DELETE FROM run_coverage_summary WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (tenant_id, workspace, run_id),
    )

    result_rows = _coverage_result_rows(results)
    if result_rows:
        api.execute_many(
            """
            INSERT INTO run_checker_coverage
              (tenant_id, workspace, run_id, account_id, region, service, checker_id,
               checker_scope, status, findings_count, duration_ms, confidence,
               completeness_pct, permission_gap_count, error_class, error_code,
               error_message, skip_reason, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            result_rows,
        )

    issue_rows = _coverage_issue_rows(issues)
    if issue_rows:
        api.execute_many(
            """
            INSERT INTO run_coverage_issues
              (tenant_id, workspace, run_id, account_id, region, service, checker_id,
               issue_type, operation, error_code, message, is_retryable, severity, payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            issue_rows,
        )

    api.execute(
        """
        INSERT INTO run_coverage_summary
          (tenant_id, workspace, run_id, targets_total, assessed_total,
           assessed_with_findings, assessed_no_issue, assessment_failed,
           skipped_total, not_assessed_total, permission_gap_count, coverage_pct,
           coverage_status, confidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            tenant_id,
            workspace,
            run_id,
            summary.targets_total,
            summary.assessed_total,
            summary.assessed_with_findings,
            summary.assessed_no_issue,
            summary.assessment_failed,
            summary.skipped_total,
            summary.not_assessed_total,
            summary.permission_gap_count,
            summary.coverage_pct,
            summary.coverage_status,
            summary.confidence,
        ),
    )
    return summary


def _persist_coverage_with_cursor(
    cur: Any,
    *,
    tenant_id: str,
    workspace: str,
    run_id: str,
    results: Sequence[CoverageResult],
    issues: Sequence[CoverageIssue],
) -> CoverageSummary:
    """Persist coverage rows and summary inside an existing transaction."""
    summary = _coverage_summary(results)
    cur.execute(
        "DELETE FROM run_checker_coverage WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (tenant_id, workspace, run_id),
    )
    cur.execute(
        "DELETE FROM run_coverage_issues WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (tenant_id, workspace, run_id),
    )
    cur.execute(
        "DELETE FROM run_coverage_summary WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (tenant_id, workspace, run_id),
    )

    result_rows = _coverage_result_rows(results)
    if result_rows:
        cur.executemany(
            """
            INSERT INTO run_checker_coverage
              (tenant_id, workspace, run_id, account_id, region, service, checker_id,
               checker_scope, status, findings_count, duration_ms, confidence,
               completeness_pct, permission_gap_count, error_class, error_code,
               error_message, skip_reason, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            result_rows,
        )

    issue_rows = _coverage_issue_rows(issues)
    if issue_rows:
        cur.executemany(
            """
            INSERT INTO run_coverage_issues
              (tenant_id, workspace, run_id, account_id, region, service, checker_id,
               issue_type, operation, error_code, message, is_retryable, severity, payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            """,
            issue_rows,
        )

    cur.execute(
        """
        INSERT INTO run_coverage_summary
          (tenant_id, workspace, run_id, targets_total, assessed_total,
           assessed_with_findings, assessed_no_issue, assessment_failed,
           skipped_total, not_assessed_total, permission_gap_count, coverage_pct,
           coverage_status, confidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            tenant_id,
            workspace,
            run_id,
            summary.targets_total,
            summary.assessed_total,
            summary.assessed_with_findings,
            summary.assessed_no_issue,
            summary.assessment_failed,
            summary.skipped_total,
            summary.not_assessed_total,
            summary.permission_gap_count,
            summary.coverage_pct,
            summary.coverage_status,
            summary.confidence,
        ),
    )
    return summary


def _refresh_remediation_impacts_best_effort(
    *,
    tenant_id: str,
    workspace: str,
    run_id: str,
    actor: str,
) -> int:
    """Refresh remediation impact snapshots after a run reaches ready state.

    This runs outside the ingest transaction to avoid coupling core ingestion
    success to remediation impact refresh failures.

    Args:
        tenant_id: Tenant scope.
        workspace: Workspace scope.
        run_id: Run identifier associated with this refresh.
        actor: System actor attributed in run event logs.

    Returns:
        Number of refreshed impact rows. Returns ``0`` when refresh fails.
    """
    try:
        with db_conn() as conn:
            refreshed = refresh_scope_action_impacts(
                conn,
                tenant_id=tenant_id,
                workspace=workspace,
                limit=_IMPACT_REFRESH_LIMIT,
            )
            append_run_event(
                conn,
                tenant_id=tenant_id,
                workspace=workspace,
                run_id=run_id,
                event_type="run.remediation_impact.refresh.completed",
                actor=actor,
                payload={
                    "refreshed_count": refreshed,
                    "limit": _IMPACT_REFRESH_LIMIT,
                },
            )
            conn.commit()
        return refreshed
    except Exception as exc:  # pragma: no cover - defensive logging path
        logger.warning(
            "Skipped remediation impact refresh for %s/%s: %s",
            tenant_id,
            workspace,
            exc,
        )
        try:
            with db_conn() as conn:
                append_run_event(
                    conn,
                    tenant_id=tenant_id,
                    workspace=workspace,
                    run_id=run_id,
                    event_type="run.remediation_impact.refresh.failed",
                    actor=actor,
                    payload={
                        "error": str(exc),
                        "limit": _IMPACT_REFRESH_LIMIT,
                    },
                )
                conn.commit()
        except Exception as event_exc:  # pragma: no cover - defensive logging path
            logger.warning(
                "Failed to persist remediation impact refresh failure event for %s/%s/%s: %s",
                tenant_id,
                workspace,
                run_id,
                event_exc,
            )
        return 0


def ingest_from_manifest(
    manifest_path: Path,
    *,
    db_api: DbApi | None = None,
    batch_size: int | None = None,
    parquet_batch_size: int | None = None,
) -> IngestStats:
    """Ingest a Parquet dataset described by a run_manifest.json."""
    worker_cfg = get_settings(reload=True).worker
    api = db_api or _default_db_api()
    batch_size = batch_size or int(worker_cfg.ingest_batch_size)
    parquet_batch_size = parquet_batch_size or int(worker_cfg.parquet_batch_size)
    allow_schema_mismatch = bool(worker_cfg.allow_schema_mismatch)

    if db_api is None and not allow_schema_mismatch:
        _ensure_db_schema_current()

    manifest = load_manifest(manifest_path)
    coverage_results, coverage_issues = _load_run_coverage(manifest)
    graph_nodes, graph_edges = _load_run_graph(manifest)
    expected_schema = int(SCHEMA_VERSION)
    if manifest.schema_version is not None:
        try:
            manifest_ver = int(manifest.schema_version)
        except (TypeError, ValueError):
            raise SystemExit(f"Invalid schema_version in manifest: {manifest.schema_version!r}") from None

        if manifest_ver != expected_schema and not allow_schema_mismatch:
            raise SystemExit(
                f"Schema mismatch: manifest={manifest_ver} expected={expected_schema}. "
                "Run `mckay migrate` (or `python -m apps.backend.db_migrate`) to update the DB, "
                "or set ALLOW_SCHEMA_MISMATCH=1 to override."
            )

    # Fail fast on mismatch: prevents ingesting with the wrong tenant/workspace.
    env_tenant = str(worker_cfg.tenant_id or "").strip()
    env_ws = str(worker_cfg.workspace or "").strip()
    if env_tenant and env_tenant != manifest.tenant_id:
        raise SystemExit(f"TENANT_ID mismatch: env={env_tenant!r} manifest={manifest.tenant_id!r}")
    if env_ws and env_ws != manifest.workspace:
        raise SystemExit(f"WORKSPACE mismatch: env={env_ws!r} manifest={manifest.workspace!r}")

    # Determine datasets to ingest (enriched only, or raw+correlated union).
    dataset_paths, dataset_label = _selected_dataset_paths(manifest)
    if not dataset_paths:
        raise SystemExit("No parquet files found for manifest datasets.")
    dataset_dir = ";".join(dataset_paths)

    raw_present = bool(manifest.out_raw and _glob_has_files(manifest.out_raw))
    correlated_present = bool(manifest.out_correlated and _glob_has_files(manifest.out_correlated))
    enriched_present = bool(manifest.out_enriched and _glob_has_files(manifest.out_enriched))

    use_copy = db_api is None and not bool(worker_cfg.ingest_disable_copy)
    if use_copy:
        return _ingest_with_copy(
            manifest=manifest,
            dataset_paths=dataset_paths,
            dataset_dir=dataset_dir,
            dataset_label=dataset_label,
            raw_present=raw_present,
            correlated_present=correlated_present,
            enriched_present=enriched_present,
            batch_size=batch_size,
            parquet_batch_size=parquet_batch_size,
        )

    run_ts = _manifest_run_ts(manifest.run_ts)

    existing = api.fetch_one(
        "SELECT status FROM runs WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
        (manifest.tenant_id, manifest.workspace, manifest.run_id),
    )
    if existing and existing[0] == "ready":
        logger.info(
            "SKIP: run already ingested: %s/%s/%s",
            manifest.tenant_id,
            manifest.workspace,
            manifest.run_id,
        )
        return IngestStats(
            dataset_used=dataset_label,
            dataset_dir=dataset_dir,
            raw_present=raw_present,
            correlated_present=correlated_present,
            enriched_present=enriched_present,
            presence_rows=0,
            latest_rows=0,
            coverage_rows=len(coverage_results),
            coverage_issue_rows=len(coverage_issues),
            graph_node_rows=len(graph_nodes),
            graph_edge_rows=len(graph_edges),
        )

    try:
        coverage_summary = _persist_coverage_with_api(
            api,
            tenant_id=manifest.tenant_id,
            workspace=manifest.workspace,
            run_id=manifest.run_id,
            results=coverage_results,
            issues=coverage_issues,
        )
        _persist_graph_with_api(
            api,
            manifest=manifest,
            nodes=graph_nodes,
            edges=graph_edges,
        )
        api.execute(
            """
            INSERT INTO runs (tenant_id, workspace, run_id, run_ts, status, artifact_prefix, ingested_at, engine_version,
                              pricing_version, pricing_source,
                              raw_present, correlated_present, enriched_present)
            VALUES (%s, %s, %s, %s, 'running', %s, NULL, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace, run_id) DO UPDATE SET
              run_ts = EXCLUDED.run_ts,
              status = 'running',
              artifact_prefix = EXCLUDED.artifact_prefix,
              engine_version = EXCLUDED.engine_version,
              pricing_version = EXCLUDED.pricing_version,
              pricing_source = EXCLUDED.pricing_source,
              raw_present = EXCLUDED.raw_present,
              correlated_present = EXCLUDED.correlated_present,
              enriched_present = EXCLUDED.enriched_present,
              ingested_at = NULL
            """,
            (
                manifest.tenant_id,
                manifest.workspace,
                manifest.run_id,
                run_ts,
                dataset_dir,
                manifest.engine_version,
                manifest.pricing_version,
                manifest.pricing_source,
                raw_present,
                correlated_present,
                enriched_present,
            ),
        )

        # Idempotence for presence rows
        api.execute(
            "DELETE FROM finding_presence WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
            (manifest.tenant_id, manifest.workspace, manifest.run_id),
        )

        parquet_files = _list_parquet_files_for_paths(dataset_paths)
        if not parquet_files:
            raise SystemExit("No parquet files found for manifest datasets.")

        dataset = ds.dataset([str(p) for p in parquet_files], format="parquet", partitioning="hive")
        schema_names = set(dataset.schema.names)

        filt = None
        if "tenant_id" in schema_names:
            filt = ds.field("tenant_id") == manifest.tenant_id
        if "workspace_id" in schema_names:
            expr = ds.field("workspace_id") == manifest.workspace
            filt = expr if filt is None else (filt & expr)
        if "run_id" in schema_names and manifest.run_id:
            expr = ds.field("run_id") == manifest.run_id
            filt = expr if filt is None else (filt & expr)

        scanner = dataset.scanner(filter=filt, batch_size=int(parquet_batch_size))

        presence_rows: list[Sequence[Any]] = []
        latest_rows: list[Sequence[Any]] = []
        seen_fingerprints: set[str] = set()
        total_presence = 0
        total_latest = 0

        def _flush_presence() -> None:
            """Flush buffered presence rows to the DB."""
            nonlocal total_presence
            if not presence_rows:
                return
            api.execute_many(
                """
                INSERT INTO finding_presence
                (tenant_id, workspace, run_id, fingerprint, check_id, service, severity, title,
                estimated_monthly_savings, region, account_id, detected_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                presence_rows,
            )
            total_presence += len(presence_rows)
            presence_rows.clear()

        def _flush_latest() -> None:
            """Flush buffered latest rows to the DB."""
            nonlocal total_latest
            if not latest_rows:
                return
            api.execute_many(
                """
                INSERT INTO finding_latest
                (tenant_id, workspace, fingerprint, run_id,
                 check_id, service, severity, title,
                 estimated_monthly_savings, region, account_id,
                 category, group_key,
                 payload, detected_at)
                VALUES
                (%s,%s,%s,%s,
                 %s,%s,%s,%s,
                 %s,%s,%s,
                 %s,%s,
                 %s::jsonb,%s)
                ON CONFLICT (tenant_id, workspace, fingerprint) DO UPDATE SET
                  run_id = EXCLUDED.run_id,
                  check_id = EXCLUDED.check_id,
                  service = EXCLUDED.service,
                  severity = EXCLUDED.severity,
                  title = EXCLUDED.title,
                  estimated_monthly_savings = EXCLUDED.estimated_monthly_savings,
                  region = EXCLUDED.region,
                  account_id = EXCLUDED.account_id,
                  category = EXCLUDED.category,
                  group_key = EXCLUDED.group_key,
                  payload = EXCLUDED.payload,
                  detected_at = EXCLUDED.detected_at
                """,
                latest_rows,
            )
            total_latest += len(latest_rows)
            latest_rows.clear()

        for batch in scanner.to_batches():
            rows = batch.to_pylist()
            for rec in rows:
                if not isinstance(rec, dict):
                    continue

                # Safety guard if filters were not applied.
                if rec.get("tenant_id") != manifest.tenant_id:
                    continue
                if rec.get("workspace_id") and rec.get("workspace_id") != manifest.workspace:
                    continue
                if rec.get("run_id") and manifest.run_id and rec.get("run_id") != manifest.run_id:
                    continue

                fp = str(rec.get("fingerprint") or "").strip()
                if not fp:
                    continue
                if fp in seen_fingerprints:
                    continue
                seen_fingerprints.add(fp)

                check_id, service, severity, title, savings_f, region, account_id, category, group_key = (
                    _guess_fields_from_record(rec)
                )

                detected_at = _parse_dt(rec.get("run_ts")) or run_ts

                presence_rows.append(
                    (
                        manifest.tenant_id,
                        manifest.workspace,
                        manifest.run_id,
                        fp,
                        check_id,
                        service,
                        severity,
                        title,
                        savings_f,
                        region,
                        account_id,
                        detected_at,
                    )
                )

                payload_json = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=_json_default)
                latest_rows.append(
                    (
                        manifest.tenant_id,
                        manifest.workspace,
                        fp,
                        manifest.run_id,
                        check_id,
                        service,
                        severity,
                        title,
                        savings_f,
                        region,
                        account_id,
                        category,
                        group_key,
                        payload_json,
                        detected_at,
                    )
                )

                if len(presence_rows) >= batch_size:
                    _flush_presence()
                if len(latest_rows) >= batch_size:
                    _flush_latest()

        _flush_presence()
        _flush_latest()

        _refresh_aggregates_with_api(
            api,
            tenant_id=manifest.tenant_id,
            workspace=manifest.workspace,
        )
        if db_api is None:
            _assert_post_ingest_invariants_with_api(
                api,
                tenant_id=manifest.tenant_id,
                workspace=manifest.workspace,
                run_id=manifest.run_id,
                expected_presence=total_presence,
                expected_latest=total_latest,
            )

        api.execute(
            """
            UPDATE runs
            SET status='ready', ingested_at=NOW(),
                raw_present=%s, correlated_present=%s, enriched_present=%s,
                coverage_pct=%s, coverage_status=%s, coverage_targets=%s,
                coverage_failed=%s, permission_gap_count=%s
            WHERE tenant_id=%s AND workspace=%s AND run_id=%s
            """,
            (
                raw_present,
                correlated_present,
                enriched_present,
                coverage_summary.coverage_pct,
                coverage_summary.coverage_status,
                coverage_summary.targets_total,
                coverage_summary.assessment_failed,
                coverage_summary.permission_gap_count,
                manifest.tenant_id,
                manifest.workspace,
                manifest.run_id,
            ),
        )
        if db_api is None:
            _refresh_remediation_impacts_best_effort(
                tenant_id=manifest.tenant_id,
                workspace=manifest.workspace,
                run_id=manifest.run_id,
                actor=default_owner("ingest_parquet"),
            )
    except Exception as exc:
        logger.exception(
            "Ingest failed for %s/%s/%s: %s",
            manifest.tenant_id,
            manifest.workspace,
            manifest.run_id,
            exc,
        )
        api.execute(
            """
            UPDATE runs
            SET status='failed'
            WHERE tenant_id=%s AND workspace=%s AND run_id=%s
            """,
            (manifest.tenant_id, manifest.workspace, manifest.run_id),
        )
        raise

    logger.info(
        "OK: ingested %s items from %s as run %s/%s/%s (presence=%s, latest=%s)",
        total_presence,
        dataset_dir,
        manifest.tenant_id,
        manifest.workspace,
        manifest.run_id,
        total_presence,
        total_latest,
    )

    return IngestStats(
        dataset_used=dataset_label,
        dataset_dir=dataset_dir,
        raw_present=raw_present,
        correlated_present=correlated_present,
        enriched_present=enriched_present,
        presence_rows=total_presence,
        latest_rows=total_latest,
        coverage_rows=len(coverage_results),
        coverage_issue_rows=len(coverage_issues),
        graph_node_rows=len(graph_nodes),
        graph_edge_rows=len(graph_edges),
    )


def _ingest_with_copy(
    *,
    manifest,
    dataset_paths: Sequence[str],
    dataset_dir: str,
    dataset_label: str,
    raw_present: bool,
    correlated_present: bool,
    enriched_present: bool,
    batch_size: int,
    parquet_batch_size: int,
) -> IngestStats:
    """Ingest using COPY into temp tables for scale."""
    run_ts = _manifest_run_ts(manifest.run_ts)
    coverage_results, coverage_issues = _load_run_coverage(manifest)
    graph_nodes, graph_edges = _load_run_graph(manifest)

    parquet_files = _list_parquet_files_for_paths(dataset_paths)
    if not parquet_files:
        raise SystemExit("No parquet files found for manifest datasets.")

    dataset = ds.dataset([str(p) for p in parquet_files], format="parquet", partitioning="hive")
    schema_names = set(dataset.schema.names)

    filt = None
    if "tenant_id" in schema_names:
        filt = ds.field("tenant_id") == manifest.tenant_id
    if "workspace_id" in schema_names:
        expr = ds.field("workspace_id") == manifest.workspace
        filt = expr if filt is None else (filt & expr)
    if "run_id" in schema_names and manifest.run_id:
        expr = ds.field("run_id") == manifest.run_id
        filt = expr if filt is None else (filt & expr)

    scanner = dataset.scanner(filter=filt, batch_size=int(parquet_batch_size))

    presence_cols = (
        "tenant_id",
        "workspace",
        "run_id",
        "fingerprint",
        "check_id",
        "service",
        "severity",
        "title",
        "estimated_monthly_savings",
        "region",
        "account_id",
        "detected_at",
    )
    latest_cols = (
        "tenant_id",
        "workspace",
        "fingerprint",
        "run_id",
        "check_id",
        "service",
        "severity",
        "title",
        "estimated_monthly_savings",
        "region",
        "account_id",
        "category",
        "group_key",
        "payload",
        "detected_at",
    )

    total_presence = 0
    total_latest = 0
    lock_owner = default_owner("ingest_parquet")
    lock_token: str | None = None

    with db_conn() as conn:
        try:
            lock = acquire_run_lock(
                conn,
                tenant_id=manifest.tenant_id,
                workspace=manifest.workspace,
                run_id=manifest.run_id,
                owner=lock_owner,
                ttl_seconds=_lock_ttl_seconds(),
            )
            if lock is None:
                raise SystemExit(
                    "Run is already being ingested (active lock). "
                    f"tenant={manifest.tenant_id} workspace={manifest.workspace} run_id={manifest.run_id}"
                )
            lock_token = lock.token
            append_run_event(
                conn,
                tenant_id=manifest.tenant_id,
                workspace=manifest.workspace,
                run_id=manifest.run_id,
                event_type="run.lock.acquired",
                actor=lock_owner,
                payload={"expires_at": lock.expires_at.isoformat()},
            )

            state = begin_run_running(
                conn,
                tenant_id=manifest.tenant_id,
                workspace=manifest.workspace,
                run_id=manifest.run_id,
                run_ts=run_ts,
                artifact_prefix=dataset_dir,
                engine_version=manifest.engine_version,
                pricing_version=manifest.pricing_version,
                pricing_source=manifest.pricing_source,
                raw_present=raw_present,
                correlated_present=correlated_present,
                enriched_present=enriched_present,
                actor=lock_owner,
            )
            if state == STATE_READY:
                released = release_run_lock(
                    conn,
                    tenant_id=manifest.tenant_id,
                    workspace=manifest.workspace,
                    run_id=manifest.run_id,
                    lock_token=lock_token,
                )
                if released:
                    append_run_event(
                        conn,
                        tenant_id=manifest.tenant_id,
                        workspace=manifest.workspace,
                        run_id=manifest.run_id,
                        event_type="run.lock.released",
                        actor=lock_owner,
                    )
                conn.commit()
                logger.info(
                    "SKIP: run already ingested: %s/%s/%s",
                    manifest.tenant_id,
                    manifest.workspace,
                    manifest.run_id,
                )
                return IngestStats(
                    dataset_used=dataset_label,
                    dataset_dir=dataset_dir,
                    raw_present=raw_present,
                    correlated_present=correlated_present,
                    enriched_present=enriched_present,
                    presence_rows=0,
                    latest_rows=0,
                    coverage_rows=len(coverage_results),
                    coverage_issue_rows=len(coverage_issues),
                    graph_node_rows=len(graph_nodes),
                    graph_edge_rows=len(graph_edges),
                )

            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM finding_presence WHERE tenant_id=%s AND workspace=%s AND run_id=%s",
                    (manifest.tenant_id, manifest.workspace, manifest.run_id),
                )

                cur.execute(
                    "CREATE TEMP TABLE tmp_presence (LIKE finding_presence INCLUDING DEFAULTS) ON COMMIT DROP"
                )
                cur.execute(
                    "CREATE TEMP TABLE tmp_latest (LIKE finding_latest INCLUDING DEFAULTS) ON COMMIT DROP"
                )

                presence_rows: list[Sequence[Any]] = []
                latest_rows: list[Sequence[Any]] = []
                seen_fingerprints: set[str] = set()

                def _flush_presence_copy() -> None:
                    nonlocal total_presence
                    if not presence_rows:
                        return
                    total_presence += _copy_rows(cur, "tmp_presence", presence_cols, presence_rows)
                    presence_rows.clear()

                def _flush_latest_copy() -> None:
                    nonlocal total_latest
                    if not latest_rows:
                        return
                    total_latest += _copy_rows(cur, "tmp_latest", latest_cols, latest_rows)
                    latest_rows.clear()

                for batch in scanner.to_batches():
                    rows = batch.to_pylist()
                    for rec in rows:
                        if not isinstance(rec, dict):
                            continue

                        if rec.get("tenant_id") != manifest.tenant_id:
                            continue
                        if rec.get("workspace_id") and rec.get("workspace_id") != manifest.workspace:
                            continue
                        if rec.get("run_id") and manifest.run_id and rec.get("run_id") != manifest.run_id:
                            continue

                        fp = str(rec.get("fingerprint") or "").strip()
                        if not fp:
                            continue
                        if fp in seen_fingerprints:
                            continue
                        seen_fingerprints.add(fp)

                        check_id, service, severity, title, savings_f, region, account_id, category, group_key = (
                            _guess_fields_from_record(rec)
                        )

                        detected_at = _parse_dt(rec.get("run_ts")) or run_ts

                        presence_rows.append(
                            (
                                manifest.tenant_id,
                                manifest.workspace,
                                manifest.run_id,
                                fp,
                                check_id,
                                service,
                                severity,
                                title,
                                savings_f,
                                region,
                                account_id,
                                detected_at,
                            )
                        )

                        payload_json = json.dumps(
                            rec,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=_json_default,
                        )
                        latest_rows.append(
                            (
                                manifest.tenant_id,
                                manifest.workspace,
                                fp,
                                manifest.run_id,
                                check_id,
                                service,
                                severity,
                                title,
                                savings_f,
                                region,
                                account_id,
                                category,
                                group_key,
                                payload_json,
                                detected_at,
                            )
                        )

                        if len(presence_rows) >= batch_size:
                            _flush_presence_copy()
                        if len(latest_rows) >= batch_size:
                            _flush_latest_copy()

                _flush_presence_copy()
                _flush_latest_copy()

                cur.execute(
                    """
                    INSERT INTO finding_presence
                    (tenant_id, workspace, run_id, fingerprint, check_id, service, severity, title,
                     estimated_monthly_savings, region, account_id, detected_at)
                    SELECT
                      tenant_id, workspace, run_id, fingerprint, check_id, service, severity, title,
                      estimated_monthly_savings, region, account_id, detected_at
                    FROM tmp_presence
                    """
                )

                cur.execute(
                    """
                    INSERT INTO finding_latest
                    (tenant_id, workspace, fingerprint, run_id,
                     check_id, service, severity, title,
                     estimated_monthly_savings, region, account_id,
                     category, group_key,
                     payload, detected_at)
                    SELECT
                      tenant_id, workspace, fingerprint, run_id,
                      check_id, service, severity, title,
                      estimated_monthly_savings, region, account_id,
                      category, group_key,
                      payload, detected_at
                    FROM tmp_latest
                    ON CONFLICT (tenant_id, workspace, fingerprint) DO UPDATE SET
                      run_id = EXCLUDED.run_id,
                      check_id = EXCLUDED.check_id,
                      service = EXCLUDED.service,
                      severity = EXCLUDED.severity,
                      title = EXCLUDED.title,
                      estimated_monthly_savings = EXCLUDED.estimated_monthly_savings,
                      region = EXCLUDED.region,
                      account_id = EXCLUDED.account_id,
                      category = EXCLUDED.category,
                      group_key = EXCLUDED.group_key,
                      payload = EXCLUDED.payload,
                      detected_at = EXCLUDED.detected_at
                    """
                )

                _refresh_aggregates_with_cursor(
                    cur,
                    tenant_id=manifest.tenant_id,
                    workspace=manifest.workspace,
                )
                coverage_summary = _persist_coverage_with_cursor(
                    cur,
                    tenant_id=manifest.tenant_id,
                    workspace=manifest.workspace,
                    run_id=manifest.run_id,
                    results=coverage_results,
                    issues=coverage_issues,
                )
                _persist_graph_with_cursor(
                    cur,
                    manifest=manifest,
                    nodes=graph_nodes,
                    edges=graph_edges,
                )
                _assert_post_ingest_invariants_with_cursor(
                    cur,
                    tenant_id=manifest.tenant_id,
                    workspace=manifest.workspace,
                    run_id=manifest.run_id,
                    expected_presence=total_presence,
                    expected_latest=total_latest,
                )

                transition_run_to_ready(
                    conn,
                    tenant_id=manifest.tenant_id,
                    workspace=manifest.workspace,
                    run_id=manifest.run_id,
                    actor=lock_owner,
                    raw_present=raw_present,
                    correlated_present=correlated_present,
                    enriched_present=enriched_present,
                )
                with conn.cursor() as run_cur:
                    run_cur.execute(
                        """
                        UPDATE runs
                        SET coverage_pct=%s,
                            coverage_status=%s,
                            coverage_targets=%s,
                            coverage_failed=%s,
                            permission_gap_count=%s
                        WHERE tenant_id=%s AND workspace=%s AND run_id=%s
                        """,
                        (
                            coverage_summary.coverage_pct,
                            coverage_summary.coverage_status,
                            coverage_summary.targets_total,
                            coverage_summary.assessment_failed,
                            coverage_summary.permission_gap_count,
                            manifest.tenant_id,
                            manifest.workspace,
                            manifest.run_id,
                        ),
                    )
                if lock_token:
                    released = release_run_lock(
                        conn,
                        tenant_id=manifest.tenant_id,
                        workspace=manifest.workspace,
                        run_id=manifest.run_id,
                        lock_token=lock_token,
                    )
                    if released:
                        append_run_event(
                            conn,
                            tenant_id=manifest.tenant_id,
                            workspace=manifest.workspace,
                            run_id=manifest.run_id,
                            event_type="run.lock.released",
                            actor=lock_owner,
                        )
                        lock_token = None

            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception as rb_exc:
                logger.warning("Rollback failed after COPY ingest error: %s", rb_exc)
            try:
                transition_run_to_failed(
                    conn,
                    tenant_id=manifest.tenant_id,
                    workspace=manifest.workspace,
                    run_id=manifest.run_id,
                    run_ts=run_ts,
                    artifact_prefix=dataset_dir,
                    engine_version=manifest.engine_version,
                    pricing_version=manifest.pricing_version,
                    pricing_source=manifest.pricing_source,
                    actor=lock_owner,
                    reason=str(exc),
                )
                if lock_token:
                    released = release_run_lock(
                        conn,
                        tenant_id=manifest.tenant_id,
                        workspace=manifest.workspace,
                        run_id=manifest.run_id,
                        lock_token=lock_token,
                    )
                    if released:
                        append_run_event(
                            conn,
                            tenant_id=manifest.tenant_id,
                            workspace=manifest.workspace,
                            run_id=manifest.run_id,
                            event_type="run.lock.released",
                            actor=lock_owner,
                        )
                conn.commit()
            except Exception as state_exc:
                try:
                    conn.rollback()
                except Exception as rb_exc:
                    logger.warning("Rollback failed while persisting failed run state: %s", rb_exc)
                logger.warning("Failed to persist failed run state: %s", state_exc)
            raise

    logger.info(
        "OK: ingested %s items from %s as run %s/%s/%s (presence=%s, latest=%s)",
        total_presence,
        dataset_dir,
        manifest.tenant_id,
        manifest.workspace,
        manifest.run_id,
        total_presence,
        total_latest,
    )
    refreshed = _refresh_remediation_impacts_best_effort(
        tenant_id=manifest.tenant_id,
        workspace=manifest.workspace,
        run_id=manifest.run_id,
        actor=lock_owner,
    )
    if refreshed:
        logger.info(
            "Refreshed remediation impacts after run ready: %s/%s (%s rows)",
            manifest.tenant_id,
            manifest.workspace,
            refreshed,
        )

    return IngestStats(
        dataset_used=dataset_label,
        dataset_dir=dataset_dir,
        raw_present=raw_present,
        correlated_present=correlated_present,
        enriched_present=enriched_present,
        presence_rows=total_presence,
        latest_rows=total_latest,
        coverage_rows=len(coverage_results),
        coverage_issue_rows=len(coverage_issues),
        graph_node_rows=len(graph_nodes),
        graph_edge_rows=len(graph_edges),
    )


def _find_manifest_path(arg: str | None) -> Path:
    """Resolve the manifest path from args, env, or cwd."""
    if arg:
        p = Path(arg).resolve()
        if not p.exists():
            raise SystemExit(f"manifest not found: {p}")
        return p

    env_path = get_settings(reload=True).worker.manifest_path
    if env_path:
        p = Path(env_path).resolve()
        if p.exists():
            return p
        raise SystemExit(f"manifest not found: {p}")

    found = find_manifest(Path.cwd())
    if found and found.exists():
        return found

    raise SystemExit("run_manifest.json not found (use --manifest or MANIFEST_PATH).")


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint."""
    import argparse

    parser = argparse.ArgumentParser(description="Ingest findings from Parquet into Postgres.")
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to run_manifest.json (or set MANIFEST_PATH).",
    )
    args = parser.parse_args(argv)

    manifest_path = _find_manifest_path(args.manifest)
    ingest_from_manifest(manifest_path)


if __name__ == "__main__":
    main()
