"""Runs Blueprint.

Provides run management endpoints for querying runs and computing diffs.
"""

from typing import Any

from flask import Blueprint, jsonify

from apps.backend.db import db_conn, fetch_all_dict_conn, fetch_one_dict_conn
from apps.flask_api.auth_middleware import require_permission
from apps.flask_api.utils import _json, _require_scope_from_query

# Create the blueprint
runs_bp = Blueprint("runs", __name__)


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


@runs_bp.route("/api/runs/latest/coverage/checkers", methods=["GET"])
@require_permission("runs:read")
def api_runs_latest_coverage_checkers() -> Any:
    """Get checker-level coverage rows for the latest run in scope."""
    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            rows = fetch_all_dict_conn(
                conn,
                """
                WITH latest_run AS (
                  SELECT run_id, run_ts
                  FROM runs
                  WHERE tenant_id = %s AND workspace = %s
                  ORDER BY run_ts DESC
                  LIMIT 1
                )
                SELECT
                  lr.run_id,
                  lr.run_ts,
                  c.account_id,
                  c.region,
                  c.service,
                  c.checker_id,
                  c.checker_scope,
                  c.status,
                  c.findings_count,
                  c.duration_ms,
                  c.confidence,
                  c.completeness_pct,
                  c.permission_gap_count,
                  c.error_class,
                  c.error_code,
                  c.error_message,
                  c.skip_reason,
                  c.started_at,
                  c.finished_at
                FROM latest_run lr
                LEFT JOIN run_checker_coverage c
                  ON c.tenant_id = %s
                 AND c.workspace = %s
                 AND c.run_id = lr.run_id
                ORDER BY
                  CASE c.status
                    WHEN 'assessment_failed' THEN 0
                    WHEN 'skipped' THEN 1
                    WHEN 'not_assessed' THEN 2
                    WHEN 'assessed_with_findings' THEN 3
                    WHEN 'assessed_no_issue' THEN 4
                    ELSE 5
                  END,
                  c.service,
                  c.region,
                  c.checker_id
                """,
                (tenant_id, workspace, tenant_id, workspace),
            )

        run = None
        items: list[dict[str, Any]] = []
        for row in rows:
            if row.get("run_id") and run is None:
                run = {
                    "run_id": row.get("run_id"),
                    "run_ts": row.get("run_ts"),
                }
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
                "run": run,
                "items": items,
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
        with db_conn() as conn:
            rows = fetch_all_dict_conn(
                conn,
                """
                WITH latest_run AS (
                  SELECT run_id, run_ts
                  FROM runs
                  WHERE tenant_id = %s AND workspace = %s
                  ORDER BY run_ts DESC
                  LIMIT 1
                )
                SELECT
                  lr.run_id,
                  lr.run_ts,
                  i.account_id,
                  i.region,
                  i.service,
                  i.checker_id,
                  i.issue_type,
                  i.operation,
                  i.error_code,
                  i.message,
                  i.is_retryable,
                  i.severity,
                  i.payload,
                  i.created_at
                FROM latest_run lr
                LEFT JOIN run_coverage_issues i
                  ON i.tenant_id = %s
                 AND i.workspace = %s
                 AND i.run_id = lr.run_id
                ORDER BY
                  CASE i.severity
                    WHEN 'error' THEN 0
                    WHEN 'warning' THEN 1
                    ELSE 2
                  END,
                  i.service,
                  i.region,
                  i.checker_id,
                  i.issue_type
                """,
                (tenant_id, workspace, tenant_id, workspace),
            )

        run = None
        items: list[dict[str, Any]] = []
        for row in rows:
            if row.get("run_id") and run is None:
                run = {
                    "run_id": row.get("run_id"),
                    "run_ts": row.get("run_ts"),
                }
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
                "run": run,
                "items": items,
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
