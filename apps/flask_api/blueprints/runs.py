"""Runs Blueprint.

Provides run management endpoints for querying runs, coverage visibility,
and computing diffs.
"""

from typing import Any

from flask import Blueprint, jsonify

from apps.backend.db import db_conn, fetch_all_dict_conn, fetch_one_dict_conn
from apps.flask_api.auth_middleware import require_permission
from apps.flask_api.graph_context import load_graph_context
from apps.flask_api.utils import (
    _coerce_non_negative_int,
    _coerce_optional_text,
    _coerce_positive_int,
    _json,
    _parse_iso8601_dt,
    _q,
    _require_scope_from_query,
)

# Create the blueprint
runs_bp = Blueprint("runs", __name__)


def _latest_run_ref(conn: Any, tenant_id: str, workspace: str) -> dict[str, Any] | None:
    """Fetch latest run id and timestamp for one tenant/workspace."""
    return fetch_one_dict_conn(
        conn,
        """
        SELECT run_id, run_ts
        FROM runs
        WHERE tenant_id = %s AND workspace = %s
        ORDER BY run_ts DESC
        LIMIT 1
        """,
        (tenant_id, workspace),
    )


def _coverage_query_filters() -> dict[str, Any]:
    """Parse supported coverage query filters from request args."""
    limit_raw = _q("limit")
    offset_raw = _q("offset")
    limit = 200 if limit_raw in (None, "") else _coerce_positive_int(limit_raw, field_name="limit")
    offset = 0 if offset_raw in (None, "") else _coerce_non_negative_int(offset_raw, field_name="offset")
    return {
        "status": _coerce_optional_text(_q("status")),
        "service": _coerce_optional_text(_q("service")),
        "region": _coerce_optional_text(_q("region")),
        "account_id": _coerce_optional_text(_q("account_id")),
        "checker_id": _coerce_optional_text(_q("checker_id")),
        "issue_type": _coerce_optional_text(_q("issue_type")),
        "limit": limit,
        "offset": offset,
    }


def _coverage_history_filters() -> dict[str, Any]:
    """Parse supported coverage history filters from request args."""
    limit_raw = _q("limit")
    limit = 20 if limit_raw in (None, "") else _coerce_positive_int(limit_raw, field_name="limit")
    return {
        "status": _coerce_optional_text(_q("status")),
        "date_from": _parse_iso8601_dt(_q("date_from"), field_name="date_from"),
        "date_to": _parse_iso8601_dt(_q("date_to"), field_name="date_to"),
        "limit": limit,
    }


def _graph_query_filters() -> dict[str, Any]:
    """Parse supported graph context query filters from request args."""
    limit_raw = _q("neighbor_limit")
    neighbor_limit = 25 if limit_raw in (None, "") else _coerce_positive_int(
        limit_raw,
        field_name="neighbor_limit",
    )
    return {
        "resource_key": _coerce_optional_text(_q("resource_key")),
        "neighbor_limit": min(neighbor_limit, 100),
    }


def _append_filter(
    clauses: list[str],
    params: list[Any],
    *,
    column_sql: str,
    value: str | None,
) -> None:
    """Append one equality filter when a non-empty value is present."""
    if value:
        clauses.append(f"{column_sql} = %s")
        params.append(value)


@runs_bp.route("/api/runs/latest", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest() -> Any:
    """Get the latest run for a tenant/workspace.

    Query params:
        tenant_id (required): Tenant identifier
        workspace (required): Workspace identifier

    Returns:
        JSON with run details or error
    """
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            row = fetch_one_dict_conn(
                conn,
                """
                SELECT tenant_id, workspace, run_id, run_ts, status, artifact_prefix,
                       ingested_at, engine_version, pricing_version, pricing_source,
                       raw_present, correlated_present, enriched_present,
                       coverage_pct, coverage_status, coverage_targets,
                       coverage_failed, permission_gap_count
                FROM runs
                WHERE tenant_id = %s AND workspace = %s
                ORDER BY run_ts DESC
                LIMIT 1
                """,
                (tenant_id, workspace),
            )
        return jsonify({"tenant_id": tenant_id, "workspace": workspace, "run": row})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@runs_bp.route("/api/runs/latest/coverage", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_coverage() -> Any:
    """Get latest run coverage summary for a tenant/workspace."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            row = fetch_one_dict_conn(
                conn,
                """
                SELECT
                  r.tenant_id,
                  r.workspace,
                  r.run_id,
                  r.run_ts,
                  r.status,
                  r.coverage_pct,
                  r.coverage_status,
                  r.coverage_targets,
                  r.coverage_failed,
                  r.permission_gap_count,
                  s.targets_total,
                  s.assessed_total,
                  s.assessed_with_findings,
                  s.assessed_no_issue,
                  s.assessment_failed,
                  s.skipped_total,
                  s.not_assessed_total,
                  s.permission_gap_count AS summary_permission_gap_count,
                  s.coverage_pct AS summary_coverage_pct,
                  s.coverage_status AS summary_coverage_status,
                  s.confidence
                FROM runs r
                LEFT JOIN run_coverage_summary s
                  ON s.tenant_id = r.tenant_id
                 AND s.workspace = r.workspace
                 AND s.run_id = r.run_id
                WHERE r.tenant_id = %s AND r.workspace = %s
                ORDER BY r.run_ts DESC
                LIMIT 1
                """,
                (tenant_id, workspace),
            )

        if not row:
            return _json(
                {
                    "ok": True,
                    "tenant_id": tenant_id,
                    "workspace": workspace,
                    "run": None,
                    "coverage": None,
                }
            )

        run = {
            "tenant_id": row.get("tenant_id"),
            "workspace": row.get("workspace"),
            "run_id": row.get("run_id"),
            "run_ts": row.get("run_ts"),
            "status": row.get("status"),
            "coverage_pct": row.get("coverage_pct"),
            "coverage_status": row.get("coverage_status"),
            "coverage_targets": row.get("coverage_targets"),
            "coverage_failed": row.get("coverage_failed"),
            "permission_gap_count": row.get("permission_gap_count"),
        }
        coverage = None
        if row.get("targets_total") is not None:
            coverage = {
                "targets_total": row.get("targets_total"),
                "assessed_total": row.get("assessed_total"),
                "assessed_with_findings": row.get("assessed_with_findings"),
                "assessed_no_issue": row.get("assessed_no_issue"),
                "assessment_failed": row.get("assessment_failed"),
                "skipped_total": row.get("skipped_total"),
                "not_assessed_total": row.get("not_assessed_total"),
                "permission_gap_count": row.get("summary_permission_gap_count"),
                "coverage_pct": row.get("summary_coverage_pct"),
                "coverage_status": row.get("summary_coverage_status"),
                "confidence": row.get("confidence"),
            }

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "run": run,
                "coverage": coverage,
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/latest/graph/context", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_graph_context() -> Any:
    """Get bounded graph context for one resource from the latest graph snapshot."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        filters = _graph_query_filters()
        resource_key = filters["resource_key"]
        if not resource_key:
            return _json(
                {
                    "error": "bad_request",
                    "message": "resource_key is required",
                },
                status=400,
            )

        with db_conn() as conn:
            latest_run = _latest_run_ref(conn, tenant_id, workspace)
            resource, neighbors, total_neighbors = load_graph_context(
                conn,
                tenant_id=tenant_id,
                workspace=workspace,
                resource_key=resource_key,
                neighbor_limit=filters["neighbor_limit"],
            )
            if not resource:
                return _json(
                    {
                        "ok": True,
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "run": latest_run,
                        "resource": None,
                        "neighbors": neighbors,
                        "total_neighbors": total_neighbors,
                        "neighbor_limit": filters["neighbor_limit"],
                    }
                )

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "run": latest_run,
                "resource": resource,
                "neighbors": neighbors,
                "total_neighbors": total_neighbors,
                "neighbor_limit": filters["neighbor_limit"],
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/latest/coverage/checkers", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_coverage_checkers() -> Any:
    """Get checker-level coverage rows for the latest run in scope."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        filters = _coverage_query_filters()
        with db_conn() as conn:
            latest_run = _latest_run_ref(conn, tenant_id, workspace)
            if not latest_run:
                return _json(
                    {
                        "ok": True,
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "run": None,
                        "items": [],
                        "total": 0,
                        "limit": filters["limit"],
                        "offset": filters["offset"],
                    }
                )

            where = ["tenant_id = %s", "workspace = %s", "run_id = %s"]
            params: list[Any] = [tenant_id, workspace, latest_run["run_id"]]
            _append_filter(where, params, column_sql="status", value=filters["status"])
            _append_filter(where, params, column_sql="service", value=filters["service"])
            _append_filter(where, params, column_sql="region", value=filters["region"])
            _append_filter(where, params, column_sql="account_id", value=filters["account_id"])
            _append_filter(where, params, column_sql="checker_id", value=filters["checker_id"])
            where_sql = " AND ".join(where)

            total_rows = fetch_all_dict_conn(
                conn,
                f"SELECT COUNT(*) AS count FROM run_checker_coverage WHERE {where_sql}",
                tuple(params),
            )
            total_row = total_rows[0] if total_rows else {"count": 0}

            rows_params = [*params, filters["limit"], filters["offset"]]
            rows = fetch_all_dict_conn(
                conn,
                f"""
                SELECT
                  account_id,
                  region,
                  service,
                  checker_id,
                  checker_scope,
                  status,
                  findings_count,
                  duration_ms,
                  confidence,
                  completeness_pct,
                  permission_gap_count,
                  error_class,
                  error_code,
                  error_message,
                  skip_reason,
                  started_at,
                  finished_at
                FROM run_checker_coverage
                WHERE {where_sql}
                ORDER BY
                  CASE status
                    WHEN 'assessment_failed' THEN 0
                    WHEN 'skipped' THEN 1
                    WHEN 'not_assessed' THEN 2
                    WHEN 'assessed_with_findings' THEN 3
                    WHEN 'assessed_no_issue' THEN 4
                    ELSE 5
                  END,
                  service,
                  region,
                  checker_id
                LIMIT %s OFFSET %s
                """,
                tuple(rows_params),
            )
        items: list[dict[str, Any]] = []
        for row in rows:
            if row.get("checker_id") is None:
                continue
            items.append(
                {
                    "account_id": row.get("account_id"),
                    "region": row.get("region"),
                    "service": row.get("service"),
                    "checker_id": row.get("checker_id"),
                    "checker_scope": row.get("checker_scope"),
                    "status": row.get("status"),
                    "findings_count": row.get("findings_count"),
                    "duration_ms": row.get("duration_ms"),
                    "confidence": row.get("confidence"),
                    "completeness_pct": row.get("completeness_pct"),
                    "permission_gap_count": row.get("permission_gap_count"),
                    "error_class": row.get("error_class"),
                    "error_code": row.get("error_code"),
                    "error_message": row.get("error_message"),
                    "skip_reason": row.get("skip_reason"),
                    "started_at": row.get("started_at"),
                    "finished_at": row.get("finished_at"),
                }
            )

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "run": latest_run,
                "items": items,
                "total": int(total_row.get("count") or 0),
                "limit": filters["limit"],
                "offset": filters["offset"],
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/latest/coverage/issues", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_coverage_issues() -> Any:
    """Get structured coverage issues for the latest run in scope."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        filters = _coverage_query_filters()
        with db_conn() as conn:
            latest_run = _latest_run_ref(conn, tenant_id, workspace)
            if not latest_run:
                return _json(
                    {
                        "ok": True,
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "run": None,
                        "items": [],
                        "total": 0,
                        "limit": filters["limit"],
                        "offset": filters["offset"],
                    }
                )

            where = ["tenant_id = %s", "workspace = %s", "run_id = %s"]
            params: list[Any] = [tenant_id, workspace, latest_run["run_id"]]
            _append_filter(where, params, column_sql="service", value=filters["service"])
            _append_filter(where, params, column_sql="region", value=filters["region"])
            _append_filter(where, params, column_sql="account_id", value=filters["account_id"])
            _append_filter(where, params, column_sql="checker_id", value=filters["checker_id"])
            _append_filter(where, params, column_sql="issue_type", value=filters["issue_type"])
            where_sql = " AND ".join(where)

            total_rows = fetch_all_dict_conn(
                conn,
                f"SELECT COUNT(*) AS count FROM run_coverage_issues WHERE {where_sql}",
                tuple(params),
            )
            total_row = total_rows[0] if total_rows else {"count": 0}

            rows_params = [*params, filters["limit"], filters["offset"]]
            rows = fetch_all_dict_conn(
                conn,
                f"""
                SELECT
                  account_id,
                  region,
                  service,
                  checker_id,
                  issue_type,
                  operation,
                  error_code,
                  message,
                  is_retryable,
                  severity,
                  payload,
                  created_at
                FROM run_coverage_issues
                WHERE {where_sql}
                ORDER BY
                  CASE severity
                    WHEN 'error' THEN 0
                    WHEN 'warning' THEN 1
                    ELSE 2
                  END,
                  service,
                  region,
                  checker_id,
                  issue_type
                LIMIT %s OFFSET %s
                """,
                tuple(rows_params),
            )
        items: list[dict[str, Any]] = []
        for row in rows:
            if row.get("issue_type") is None:
                continue
            items.append(
                {
                    "account_id": row.get("account_id"),
                    "region": row.get("region"),
                    "service": row.get("service"),
                    "checker_id": row.get("checker_id"),
                    "issue_type": row.get("issue_type"),
                    "operation": row.get("operation"),
                    "error_code": row.get("error_code"),
                    "message": row.get("message"),
                    "is_retryable": row.get("is_retryable"),
                    "severity": row.get("severity"),
                    "payload": row.get("payload"),
                    "created_at": row.get("created_at"),
                }
            )

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "run": latest_run,
                "items": items,
                "total": int(total_row.get("count") or 0),
                "limit": filters["limit"],
                "offset": filters["offset"],
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/latest/coverage/services", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_coverage_services() -> Any:
    """Get service-level coverage rollups for the latest run in scope."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        filters = _coverage_query_filters()
        with db_conn() as conn:
            latest_run = _latest_run_ref(conn, tenant_id, workspace)
            if not latest_run:
                return _json(
                    {
                        "ok": True,
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "run": None,
                        "items": [],
                    }
                )

            where = ["tenant_id = %s", "workspace = %s", "run_id = %s"]
            params: list[Any] = [tenant_id, workspace, latest_run["run_id"]]
            _append_filter(where, params, column_sql="status", value=filters["status"])
            _append_filter(where, params, column_sql="region", value=filters["region"])
            _append_filter(where, params, column_sql="account_id", value=filters["account_id"])
            where_sql = " AND ".join(where)

            rows = fetch_all_dict_conn(
                conn,
                f"""
                SELECT
                  service,
                  COUNT(*) AS targets_total,
                  SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END) AS assessed_total,
                  SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) AS assessment_failed,
                  SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_total,
                  SUM(CASE WHEN status = 'not_assessed' THEN 1 ELSE 0 END) AS not_assessed_total,
                  SUM(permission_gap_count) AS permission_gap_count,
                  ROUND(
                    (
                      SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END)::numeric
                      / NULLIF(COUNT(*), 0)
                    ) * 100.0,
                    2
                  ) AS coverage_pct,
                  CASE
                    WHEN COUNT(*) = 0 OR SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) = COUNT(*) THEN 'failed'
                    WHEN SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) > 0
                      OR ROUND(
                        (
                          SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END)::numeric
                          / NULLIF(COUNT(*), 0)
                        ) * 100.0,
                        2
                      ) < 80.0 THEN 'degraded'
                    WHEN SUM(CASE WHEN status IN ('skipped', 'not_assessed') THEN 1 ELSE 0 END) > 0 THEN 'partial'
                    ELSE 'healthy'
                  END AS coverage_status
                FROM run_checker_coverage
                WHERE {where_sql}
                GROUP BY service
                ORDER BY
                  CASE
                    WHEN SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) > 0 THEN 0
                    WHEN SUM(permission_gap_count) > 0 THEN 1
                    ELSE 2
                  END,
                  service
                """,
                tuple(params),
            )

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "run": latest_run,
                "items": rows,
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/latest/coverage/accounts", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_coverage_accounts() -> Any:
    """Get account/region coverage rollups for the latest run in scope."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        filters = _coverage_query_filters()
        with db_conn() as conn:
            latest_run = _latest_run_ref(conn, tenant_id, workspace)
            if not latest_run:
                return _json(
                    {
                        "ok": True,
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "run": None,
                        "items": [],
                    }
                )

            where = ["tenant_id = %s", "workspace = %s", "run_id = %s"]
            params: list[Any] = [tenant_id, workspace, latest_run["run_id"]]
            _append_filter(where, params, column_sql="status", value=filters["status"])
            _append_filter(where, params, column_sql="service", value=filters["service"])
            _append_filter(where, params, column_sql="region", value=filters["region"])
            _append_filter(where, params, column_sql="account_id", value=filters["account_id"])
            where_sql = " AND ".join(where)

            rows = fetch_all_dict_conn(
                conn,
                f"""
                SELECT
                  account_id,
                  region,
                  COUNT(*) AS targets_total,
                  SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END) AS assessed_total,
                  SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) AS assessment_failed,
                  SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_total,
                  SUM(CASE WHEN status = 'not_assessed' THEN 1 ELSE 0 END) AS not_assessed_total,
                  SUM(permission_gap_count) AS permission_gap_count,
                  ROUND(
                    (
                      SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END)::numeric
                      / NULLIF(COUNT(*), 0)
                    ) * 100.0,
                    2
                  ) AS coverage_pct,
                  CASE
                    WHEN COUNT(*) = 0 OR SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) = COUNT(*) THEN 'failed'
                    WHEN SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) > 0
                      OR ROUND(
                        (
                          SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END)::numeric
                          / NULLIF(COUNT(*), 0)
                        ) * 100.0,
                        2
                      ) < 80.0 THEN 'degraded'
                    WHEN SUM(CASE WHEN status IN ('skipped', 'not_assessed') THEN 1 ELSE 0 END) > 0 THEN 'partial'
                    ELSE 'healthy'
                  END AS coverage_status
                FROM run_checker_coverage
                WHERE {where_sql}
                GROUP BY account_id, region
                ORDER BY
                  CASE
                    WHEN SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) > 0 THEN 0
                    WHEN SUM(permission_gap_count) > 0 THEN 1
                    ELSE 2
                  END,
                  account_id,
                  region
                """,
                tuple(params),
            )

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "run": latest_run,
                "items": rows,
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/coverage/history", methods=["GET"])
@require_permission("runs:read")
def api_runs_coverage_history() -> Any:
    """Get bounded coverage history for runs in scope."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        filters = _coverage_history_filters()
        where = ["r.tenant_id = %s", "r.workspace = %s"]
        params: list[Any] = [tenant_id, workspace]
        if filters["status"]:
            where.append("COALESCE(s.coverage_status, r.coverage_status) = %s")
            params.append(filters["status"])
        if filters["date_from"] is not None:
            where.append("r.run_ts >= %s")
            params.append(filters["date_from"])
        if filters["date_to"] is not None:
            where.append("r.run_ts <= %s")
            params.append(filters["date_to"])
        where_sql = " AND ".join(where)

        with db_conn() as conn:
            rows = fetch_all_dict_conn(
                conn,
                f"""
                SELECT
                  r.run_id,
                  r.run_ts,
                  r.status,
                  COALESCE(s.targets_total, r.coverage_targets, 0) AS targets_total,
                  COALESCE(s.assessed_total, 0) AS assessed_total,
                  COALESCE(s.assessment_failed, r.coverage_failed, 0) AS assessment_failed,
                  COALESCE(s.skipped_total, 0) AS skipped_total,
                  COALESCE(s.not_assessed_total, 0) AS not_assessed_total,
                  COALESCE(s.permission_gap_count, r.permission_gap_count, 0) AS permission_gap_count,
                  COALESCE(s.coverage_pct, r.coverage_pct, 0) AS coverage_pct,
                  COALESCE(s.coverage_status, r.coverage_status) AS coverage_status,
                  COALESCE(s.confidence, 'none') AS confidence
                FROM runs r
                LEFT JOIN run_coverage_summary s
                  ON s.tenant_id = r.tenant_id
                 AND s.workspace = r.workspace
                 AND s.run_id = r.run_id
                WHERE {where_sql}
                ORDER BY r.run_ts DESC
                LIMIT %s
                """,
                tuple([*params, filters["limit"]]),
            )

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "items": rows,
                "limit": filters["limit"],
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/coverage/regressions/latest", methods=["GET"])
@require_permission("runs:read")
def api_runs_coverage_regressions_latest() -> Any:
    """Compare the latest two ready runs for coverage regressions."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            runs = fetch_all_dict_conn(
                conn,
                """
                SELECT r.run_id, r.run_ts
                FROM runs r
                WHERE r.tenant_id = %s AND r.workspace = %s AND r.status = 'ready'
                ORDER BY r.run_ts DESC
                LIMIT 2
                """,
                (tenant_id, workspace),
            )

            if not runs or len(runs) < 2:
                return _json(
                    {
                        "ok": True,
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "runs": runs or [],
                        "summary": None,
                        "service_regressions": [],
                        "checker_regressions": {"count": 0},
                        "message": "Need at least 2 ready runs to compute coverage regressions.",
                    }
                )

            latest_run = runs[0]
            previous_run = runs[1]

            summary_rows = fetch_all_dict_conn(
                conn,
                """
                SELECT
                  run_id,
                  targets_total,
                  assessed_total,
                  assessment_failed,
                  skipped_total,
                  not_assessed_total,
                  permission_gap_count,
                  coverage_pct,
                  coverage_status,
                  confidence
                FROM run_coverage_summary
                WHERE tenant_id = %s
                  AND workspace = %s
                  AND run_id IN (%s, %s)
                """,
                (tenant_id, workspace, latest_run["run_id"], previous_run["run_id"]),
            )
            summary_by_run = {str(row["run_id"]): row for row in summary_rows}
            latest_summary = summary_by_run.get(str(latest_run["run_id"]))
            previous_summary = summary_by_run.get(str(previous_run["run_id"]))

            service_rows = fetch_all_dict_conn(
                conn,
                """
                SELECT
                  run_id,
                  service,
                  COUNT(*) AS targets_total,
                  SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) AS assessment_failed,
                  SUM(permission_gap_count) AS permission_gap_count,
                  ROUND(
                    (
                      SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END)::numeric
                      / NULLIF(COUNT(*), 0)
                    ) * 100.0,
                    2
                  ) AS coverage_pct,
                  CASE
                    WHEN COUNT(*) = 0 OR SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) = COUNT(*) THEN 'failed'
                    WHEN SUM(CASE WHEN status = 'assessment_failed' THEN 1 ELSE 0 END) > 0
                      OR ROUND(
                        (
                          SUM(CASE WHEN status IN ('assessed_with_findings', 'assessed_no_issue') THEN 1 ELSE 0 END)::numeric
                          / NULLIF(COUNT(*), 0)
                        ) * 100.0,
                        2
                      ) < 80.0 THEN 'degraded'
                    WHEN SUM(CASE WHEN status IN ('skipped', 'not_assessed') THEN 1 ELSE 0 END) > 0 THEN 'partial'
                    ELSE 'healthy'
                  END AS coverage_status
                FROM run_checker_coverage
                WHERE tenant_id = %s
                  AND workspace = %s
                  AND run_id IN (%s, %s)
                GROUP BY run_id, service
                """,
                (tenant_id, workspace, latest_run["run_id"], previous_run["run_id"]),
            )

            checker_rows = fetch_all_dict_conn(
                conn,
                """
                SELECT run_id, service, region, account_id, checker_id, status, permission_gap_count
                FROM run_checker_coverage
                WHERE tenant_id = %s
                  AND workspace = %s
                  AND run_id IN (%s, %s)
                """,
                (tenant_id, workspace, latest_run["run_id"], previous_run["run_id"]),
            )

        def _status_rank(value: str | None) -> int:
            mapping = {"healthy": 0, "partial": 1, "degraded": 2, "failed": 3}
            return mapping.get(str(value or ""), 0)

        latest_service: dict[str, dict[str, Any]] = {}
        previous_service: dict[str, dict[str, Any]] = {}
        for row in service_rows:
            key = str(row.get("service") or "")
            if str(row.get("run_id")) == str(latest_run["run_id"]):
                latest_service[key] = row
            elif str(row.get("run_id")) == str(previous_run["run_id"]):
                previous_service[key] = row

        service_regressions: list[dict[str, Any]] = []
        for service in sorted(set(latest_service) | set(previous_service)):
            latest_item = latest_service.get(service)
            previous_item = previous_service.get(service)
            if not latest_item or not previous_item:
                continue
            coverage_pct_delta = float(latest_item.get("coverage_pct") or 0) - float(previous_item.get("coverage_pct") or 0)
            assessment_failed_delta = int(latest_item.get("assessment_failed") or 0) - int(previous_item.get("assessment_failed") or 0)
            permission_gap_delta = int(latest_item.get("permission_gap_count") or 0) - int(previous_item.get("permission_gap_count") or 0)
            status_worsened = _status_rank(latest_item.get("coverage_status")) > _status_rank(previous_item.get("coverage_status"))
            if coverage_pct_delta < 0 or assessment_failed_delta > 0 or permission_gap_delta > 0 or status_worsened:
                service_regressions.append(
                    {
                        "service": service,
                        "latest": latest_item,
                        "previous": previous_item,
                        "coverage_pct_delta": round(coverage_pct_delta, 2),
                        "assessment_failed_delta": assessment_failed_delta,
                        "permission_gap_delta": permission_gap_delta,
                        "status_worsened": status_worsened,
                    }
                )

        latest_checker: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        previous_checker: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in checker_rows:
            key = (
                str(row.get("service") or ""),
                str(row.get("region") or ""),
                str(row.get("account_id") or ""),
                str(row.get("checker_id") or ""),
            )
            if str(row.get("run_id")) == str(latest_run["run_id"]):
                latest_checker[key] = row
            elif str(row.get("run_id")) == str(previous_run["run_id"]):
                previous_checker[key] = row

        checker_regressed_count = 0
        for key, latest_item in latest_checker.items():
            previous_item = previous_checker.get(key)
            if not previous_item:
                continue
            latest_failed = str(latest_item.get("status") or "") == "assessment_failed"
            previous_failed = str(previous_item.get("status") or "") == "assessment_failed"
            latest_gap = int(latest_item.get("permission_gap_count") or 0)
            previous_gap = int(previous_item.get("permission_gap_count") or 0)
            if (latest_failed and not previous_failed) or latest_gap > previous_gap:
                checker_regressed_count += 1

        summary = None
        if latest_summary and previous_summary:
            coverage_pct_delta = float(latest_summary.get("coverage_pct") or 0) - float(previous_summary.get("coverage_pct") or 0)
            assessment_failed_delta = int(latest_summary.get("assessment_failed") or 0) - int(previous_summary.get("assessment_failed") or 0)
            permission_gap_delta = int(latest_summary.get("permission_gap_count") or 0) - int(previous_summary.get("permission_gap_count") or 0)
            status_worsened = _status_rank(latest_summary.get("coverage_status")) > _status_rank(previous_summary.get("coverage_status"))
            severity = "minor"
            if coverage_pct_delta <= -10 or assessment_failed_delta >= 5 or permission_gap_delta >= 5:
                severity = "critical"
            elif coverage_pct_delta <= -3 or assessment_failed_delta > 0 or permission_gap_delta > 0 or status_worsened:
                severity = "meaningful"
            summary = {
                "latest": latest_summary,
                "previous": previous_summary,
                "coverage_pct_delta": round(coverage_pct_delta, 2),
                "assessment_failed_delta": assessment_failed_delta,
                "permission_gap_delta": permission_gap_delta,
                "status_worsened": status_worsened,
                "severity": severity,
            }

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "runs": [
                    {"run_id": latest_run["run_id"], "run_ts": latest_run["run_ts"]},
                    {"run_id": previous_run["run_id"], "run_ts": previous_run["run_ts"]},
                ],
                "summary": summary,
                "service_regressions": service_regressions,
                "checker_regressions": {"count": checker_regressed_count},
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)


@runs_bp.route("/api/runs/diff/latest", methods=["GET"])
@require_permission("runs:read")
def api_runs_diff_latest() -> Any:
    """Compute a best-effort diff between the latest two *ready* runs.

    Returns counts for:
    - new: fingerprints present in latest run but not in previous
    - disappeared: fingerprints present in previous run but not in latest

    Notes:
    - Uses finding_presence for membership (history).
    - Attributes category/check_id/service from finding_current (canonical read model).

    Query params:
        tenant_id (required): Tenant identifier
        workspace (required): Workspace identifier

    Returns:
        JSON with diff results or error
    """
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            runs = fetch_all_dict_conn(
                conn,
                """
                SELECT run_id, run_ts
                FROM runs
                WHERE tenant_id=%s AND workspace=%s AND status='ready'
                ORDER BY run_ts DESC
                LIMIT 2
                """,
                (tenant_id, workspace),
            )

            if not runs or len(runs) < 2:
                return _json(
                    {
                        "tenant_id": tenant_id,
                        "workspace": workspace,
                        "ok": True,
                        "message": "Need at least 2 ready runs to compute a diff.",
                        "runs": runs or [],
                        "new": {"count": 0, "by_category": {}, "by_check_id": {}, "by_service": {}},
                        "disappeared": {"count": 0, "by_category": {}, "by_check_id": {}, "by_service": {}},
                    }
                )

            run_new = str(runs[0]["run_id"])
            run_old = str(runs[1]["run_id"])

            new_rows = fetch_all_dict_conn(
                conn,
                """
                WITH new_fps AS (
                  SELECT p1.fingerprint
                  FROM finding_presence p1
                  WHERE p1.tenant_id=%s AND p1.workspace=%s AND p1.run_id=%s
                  EXCEPT
                  SELECT p0.fingerprint
                  FROM finding_presence p0
                  WHERE p0.tenant_id=%s AND p0.workspace=%s AND p0.run_id=%s
                )
                SELECT
                  COALESCE(fc.category, 'other') AS category,
                  COALESCE(fc.check_id, 'unknown') AS check_id,
                  COALESCE(fc.service, 'unknown') AS service,
                  COUNT(*)::bigint AS count
                FROM new_fps nf
                LEFT JOIN finding_current fc
                  ON fc.tenant_id=%s AND fc.workspace=%s AND fc.fingerprint=nf.fingerprint
                GROUP BY 1,2,3
                """,
                (tenant_id, workspace, run_new, tenant_id, workspace, run_old, tenant_id, workspace),
            )

            gone_rows = fetch_all_dict_conn(
                conn,
                """
                WITH gone_fps AS (
                  SELECT p0.fingerprint
                  FROM finding_presence p0
                  WHERE p0.tenant_id=%s AND p0.workspace=%s AND p0.run_id=%s
                  EXCEPT
                  SELECT p1.fingerprint
                  FROM finding_presence p1
                  WHERE p1.tenant_id=%s AND p1.workspace=%s AND p1.run_id=%s
                )
                SELECT
                  COALESCE(fc.category, 'other') AS category,
                  COALESCE(fc.check_id, 'unknown') AS check_id,
                  COALESCE(fc.service, 'unknown') AS service,
                  COUNT(*)::bigint AS count
                FROM gone_fps gf
                LEFT JOIN finding_current fc
                  ON fc.tenant_id=%s AND fc.workspace=%s AND fc.fingerprint=gf.fingerprint
                GROUP BY 1,2,3
                """,
                (tenant_id, workspace, run_old, tenant_id, workspace, run_new, tenant_id, workspace),
            )

        def _rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
            total = 0
            by_cat: dict[str, int] = {}
            by_check: dict[str, int] = {}
            by_svc: dict[str, int] = {}
            for r in rows:
                c = int(r.get("count") or 0)
                total += c
                cat = str(r.get("category") or "other")
                chk = str(r.get("check_id") or "unknown")
                svc = str(r.get("service") or "unknown")
                by_cat[cat] = by_cat.get(cat, 0) + c
                by_check[chk] = by_check.get(chk, 0) + c
                by_svc[svc] = by_svc.get(svc, 0) + c
            return {
                "count": total,
                "by_category": by_cat,
                "by_check_id": by_check,
                "by_service": by_svc,
                "rows": rows,
            }

        return _json(
            {
                "tenant_id": tenant_id,
                "workspace": workspace,
                "ok": True,
                "runs": [
                    {"run_id": run_new, "run_ts": runs[0]["run_ts"]},
                    {"run_id": run_old, "run_ts": runs[1]["run_ts"]},
                ],
                "new": _rollup(new_rows),
                "disappeared": _rollup(gone_rows),
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)
