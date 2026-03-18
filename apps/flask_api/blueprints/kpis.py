"""KPI Blueprint.

Provides initial value-reporting KPI endpoints for the SaaS product surface.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint

from apps.backend.db import db_conn, fetch_all_dict_conn, fetch_one_dict_conn
from apps.flask_api.auth_middleware import require_permission
from apps.flask_api.blueprints import recommendations as recommendations_module
from apps.flask_api.blueprints import remediations as remediations_module
from apps.flask_api.utils import _json, _require_scope_from_query

kpis_bp = Blueprint("kpis", __name__)


def _recommendation_rule_ids() -> list[str]:
    """Return sorted recommendation-eligible check ids."""

    rules = getattr(recommendations_module, "_RECOMMENDATION_RULES", {})
    if not isinstance(rules, dict):
        return []
    return sorted(str(check_id) for check_id in rules if str(check_id).strip())


def _priority_p1_rule_ids() -> list[str]:
    """Return sorted p1 recommendation-eligible check ids."""

    rules = getattr(recommendations_module, "_RECOMMENDATION_RULES", {})
    if not isinstance(rules, dict):
        return []
    return sorted(
        str(check_id)
        for check_id, rule in rules.items()
        if str(check_id).strip()
        and isinstance(rule, dict)
        and str(rule.get("priority") or "").strip().lower() == "p1"
    )


def _latest_run_context(conn: Any, *, tenant_id: str, workspace: str) -> dict[str, Any] | None:
    """Fetch the latest run reference for one tenant/workspace."""

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


def _latest_two_ready_runs(conn: Any, *, tenant_id: str, workspace: str) -> list[dict[str, Any]]:
    """Fetch the latest two ready runs for one tenant/workspace."""

    return fetch_all_dict_conn(
        conn,
        """
        SELECT run_id, run_ts
        FROM runs
        WHERE tenant_id = %s
          AND workspace = %s
          AND status = 'ready'
        ORDER BY run_ts DESC
        LIMIT 2
        """,
        (tenant_id, workspace),
    )


def _findings_kpis(conn: Any, *, tenant_id: str, workspace: str) -> dict[str, Any]:
    """Return open findings KPI family from finding_current."""

    row = fetch_one_dict_conn(
        conn,
        """
        SELECT
          COUNT(*)::bigint AS open_findings_count,
          SUM(CASE WHEN LOWER(COALESCE(severity, '')) IN ('critical', 'high') THEN 1 ELSE 0 END)::bigint AS needs_attention_count,
          COALESCE(
            SUM(
              CASE
                WHEN COALESCE(estimated_monthly_savings, 0) > 0 THEN estimated_monthly_savings
                ELSE 0
              END
            ),
            0
          )::double precision AS estimated_monthly_savings
        FROM finding_current
        WHERE tenant_id = %s
          AND workspace = %s
          AND effective_state = 'open'
        """,
        (tenant_id, workspace),
    ) or {}
    return {
        "source": "finding_current",
        "definition": "Open findings and open finding-estimated savings in the current tenant/workspace scope.",
        "open_findings_count": int(row.get("open_findings_count") or 0),
        "needs_attention_count": int(row.get("needs_attention_count") or 0),
        "estimated_monthly_savings": float(row.get("estimated_monthly_savings") or 0.0),
    }


def _findings_trend(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    latest_run_id: str,
    previous_run_id: str,
) -> dict[str, Any]:
    """Return finding-presence trend deltas between the latest two ready runs."""

    new_row = fetch_one_dict_conn(
        conn,
        """
        SELECT COUNT(*)::bigint AS n
        FROM (
          SELECT p1.fingerprint
          FROM finding_presence p1
          WHERE p1.tenant_id = %s AND p1.workspace = %s AND p1.run_id = %s
          EXCEPT
          SELECT p0.fingerprint
          FROM finding_presence p0
          WHERE p0.tenant_id = %s AND p0.workspace = %s AND p0.run_id = %s
        ) diff
        """,
        (tenant_id, workspace, latest_run_id, tenant_id, workspace, previous_run_id),
    ) or {}
    disappeared_row = fetch_one_dict_conn(
        conn,
        """
        SELECT COUNT(*)::bigint AS n
        FROM (
          SELECT p0.fingerprint
          FROM finding_presence p0
          WHERE p0.tenant_id = %s AND p0.workspace = %s AND p0.run_id = %s
          EXCEPT
          SELECT p1.fingerprint
          FROM finding_presence p1
          WHERE p1.tenant_id = %s AND p1.workspace = %s AND p1.run_id = %s
        ) diff
        """,
        (tenant_id, workspace, previous_run_id, tenant_id, workspace, latest_run_id),
    ) or {}
    new_count = int(new_row.get("n") or 0)
    disappeared_count = int(disappeared_row.get("n") or 0)
    return {
        "definition": "Run-to-run finding membership changes based on finding_presence for the latest two ready runs.",
        "new_count": new_count,
        "disappeared_count": disappeared_count,
        "net_change": new_count - disappeared_count,
    }


def _recommendations_kpis(conn: Any, *, tenant_id: str, workspace: str) -> dict[str, Any]:
    """Return recommendation-eligible KPI family from current findings."""

    eligible_check_ids = _recommendation_rule_ids()
    p1_check_ids = _priority_p1_rule_ids()
    if not eligible_check_ids:
        return {
            "source": "finding_current + recommendation_rules",
            "definition": "Open findings that are eligible for recommendation materialization under current recommendation rules.",
            "eligible_recommendations_count": 0,
            "priority_p1_count": 0,
            "estimated_monthly_savings": 0.0,
        }

    row = fetch_one_dict_conn(
        conn,
        """
        SELECT
          COUNT(*)::bigint AS eligible_recommendations_count,
          SUM(CASE WHEN check_id = ANY(%s) THEN 1 ELSE 0 END)::bigint AS priority_p1_count,
          COALESCE(
            SUM(
              CASE
                WHEN COALESCE(estimated_monthly_savings, 0) > 0 THEN estimated_monthly_savings
                ELSE 0
              END
            ),
            0
          )::double precision AS estimated_monthly_savings
        FROM finding_current
        WHERE tenant_id = %s
          AND workspace = %s
          AND effective_state = 'open'
          AND check_id = ANY(%s)
        """,
        (p1_check_ids, tenant_id, workspace, eligible_check_ids),
    ) or {}
    return {
        "source": "finding_current + recommendation_rules",
        "definition": "Open findings eligible for recommendation normalization. This KPI does not depend on graph packaging or suppression.",
        "eligible_recommendations_count": int(row.get("eligible_recommendations_count") or 0),
        "priority_p1_count": int(row.get("priority_p1_count") or 0),
        "estimated_monthly_savings": float(row.get("estimated_monthly_savings") or 0.0),
    }


def _recommendations_trend(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    latest_run_id: str,
    previous_run_id: str,
) -> dict[str, Any]:
    """Return recommendation-eligible run-snapshot deltas between the latest two ready runs."""

    eligible_check_ids = _recommendation_rule_ids()
    if not eligible_check_ids:
        return {
            "definition": "Run-to-run recommendation eligibility deltas derived from finding snapshots under current recommendation rules.",
            "eligible_count_delta": 0,
            "estimated_monthly_savings_delta": 0.0,
        }

    rows = fetch_all_dict_conn(
        conn,
        """
        SELECT
          fl.run_id,
          COUNT(*)::bigint AS eligible_count,
          COALESCE(
            SUM(
              CASE
                WHEN COALESCE(fl.estimated_monthly_savings, 0) > 0 THEN fl.estimated_monthly_savings
                ELSE 0
              END
            ),
            0
          )::double precision AS estimated_monthly_savings
        FROM finding_latest fl
        WHERE fl.tenant_id = %s
          AND fl.workspace = %s
          AND fl.run_id = ANY(%s)
          AND fl.check_id = ANY(%s)
        GROUP BY fl.run_id
        """,
        (tenant_id, workspace, [latest_run_id, previous_run_id], eligible_check_ids),
    )
    by_run = {str(row.get("run_id") or ""): row for row in rows}
    latest_row = by_run.get(latest_run_id, {})
    previous_row = by_run.get(previous_run_id, {})
    latest_count = int(latest_row.get("eligible_count") or 0)
    previous_count = int(previous_row.get("eligible_count") or 0)
    latest_savings = float(latest_row.get("estimated_monthly_savings") or 0.0)
    previous_savings = float(previous_row.get("estimated_monthly_savings") or 0.0)
    return {
        "definition": "Run-to-run recommendation eligibility deltas derived from finding snapshots under current recommendation rules.",
        "eligible_count_delta": latest_count - previous_count,
        "estimated_monthly_savings_delta": round(latest_savings - previous_savings, 2),
    }


def _coverage_kpis(conn: Any, *, tenant_id: str, workspace: str) -> dict[str, Any]:
    """Return latest run coverage KPI family."""

    row = fetch_one_dict_conn(
        conn,
        """
        SELECT
          s.coverage_pct,
          s.coverage_status,
          s.permission_gap_count,
          s.assessment_failed,
          s.targets_total,
          s.assessed_total,
          s.confidence,
          r.run_id,
          r.run_ts
        FROM run_coverage_summary s
        JOIN runs r
          ON r.tenant_id = s.tenant_id
         AND r.workspace = s.workspace
         AND r.run_id = s.run_id
        WHERE s.tenant_id = %s
          AND s.workspace = %s
        ORDER BY r.run_ts DESC
        LIMIT 1
        """,
        (tenant_id, workspace),
    ) or {}
    return {
        "source": "run_coverage_summary",
        "definition": "Latest run coverage and assessment trust indicators for the current tenant/workspace scope.",
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
        "coverage_status": row.get("coverage_status"),
        "permission_gap_count": int(row.get("permission_gap_count") or 0),
        "assessment_failed": int(row.get("assessment_failed") or 0),
        "targets_total": int(row.get("targets_total") or 0),
        "assessed_total": int(row.get("assessed_total") or 0),
        "confidence": row.get("confidence"),
        "latest_run_id": row.get("run_id"),
        "latest_run_ts": row.get("run_ts"),
    }


def _coverage_trend(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    latest_run_id: str,
    previous_run_id: str,
) -> dict[str, Any] | None:
    """Return latest-vs-previous coverage deltas when both summaries exist."""

    rows = fetch_all_dict_conn(
        conn,
        """
        SELECT run_id, coverage_pct, assessment_failed, permission_gap_count, coverage_status
        FROM run_coverage_summary
        WHERE tenant_id = %s
          AND workspace = %s
          AND run_id = ANY(%s)
        """,
        (tenant_id, workspace, [latest_run_id, previous_run_id]),
    )
    by_run = {str(row.get("run_id") or ""): row for row in rows}
    latest_row = by_run.get(latest_run_id)
    previous_row = by_run.get(previous_run_id)
    if not latest_row or not previous_row:
        return None
    return {
        "definition": "Coverage summary delta between the latest two ready runs.",
        "coverage_pct_delta": round(
            float(latest_row.get("coverage_pct") or 0.0) - float(previous_row.get("coverage_pct") or 0.0),
            2,
        ),
        "assessment_failed_delta": int(latest_row.get("assessment_failed") or 0)
        - int(previous_row.get("assessment_failed") or 0),
        "permission_gap_delta": int(latest_row.get("permission_gap_count") or 0)
        - int(previous_row.get("permission_gap_count") or 0),
        "latest_coverage_status": latest_row.get("coverage_status"),
        "previous_coverage_status": previous_row.get("coverage_status"),
    }


def _realized_kpis(conn: Any, *, tenant_id: str, workspace: str) -> dict[str, Any]:
    """Return realized savings KPI family from remediation impact."""

    row = fetch_one_dict_conn(
        conn,
        """
        SELECT
          COUNT(*)::bigint AS actions_count,
          SUM(CASE WHEN verification_status = 'verified_resolved' THEN 1 ELSE 0 END)::bigint AS fully_realized_count,
          SUM(CASE WHEN verification_status = 'verified_persistent' AND COALESCE(realized_monthly_savings, 0) > 0 THEN 1 ELSE 0 END)::bigint AS partial_realization_count,
          SUM(CASE WHEN verification_status = 'verified_persistent' AND COALESCE(realized_monthly_savings, 0) <= 0 THEN 1 ELSE 0 END)::bigint AS no_realization_count,
          SUM(CASE WHEN verification_status = 'pending_post_run' THEN 1 ELSE 0 END)::bigint AS pending_count,
          SUM(CASE WHEN verification_status = 'execution_failed' THEN 1 ELSE 0 END)::bigint AS failed_count,
          COALESCE(SUM(baseline_estimated_monthly_savings), 0)::double precision AS baseline_total_monthly_savings,
          COALESCE(SUM(realized_monthly_savings), 0)::double precision AS realized_total_monthly_savings,
          COALESCE(
            SUM(GREATEST(baseline_estimated_monthly_savings - COALESCE(realized_monthly_savings, 0), 0)),
            0
          )::double precision AS estimated_not_realized_monthly_savings
        FROM remediation_impact
        WHERE tenant_id = %s
          AND workspace = %s
        """,
        (tenant_id, workspace),
    ) or {}
    payload = remediations_module._impact_summary_payload(row)
    return {
        "source": "remediation_impact",
        "definition": "Tracked remediation outcomes and realized monthly savings for the current tenant/workspace scope.",
        **payload,
    }


@kpis_bp.route("/api/kpis/initial-value", methods=["GET"])
@require_permission("findings:read")
def api_kpis_initial_value() -> Any:
    """Return the initial value-reporting KPI families for one tenant/workspace."""

    try:
        tenant_id, workspace = _require_scope_from_query()
        with db_conn() as conn:
            latest_run = _latest_run_context(conn, tenant_id=tenant_id, workspace=workspace)
            ready_runs = _latest_two_ready_runs(conn, tenant_id=tenant_id, workspace=workspace)
            findings = _findings_kpis(conn, tenant_id=tenant_id, workspace=workspace)
            recommendations = _recommendations_kpis(conn, tenant_id=tenant_id, workspace=workspace)
            realized = _realized_kpis(conn, tenant_id=tenant_id, workspace=workspace)
            coverage = _coverage_kpis(conn, tenant_id=tenant_id, workspace=workspace)
            trend = None
            if len(ready_runs) >= 2:
                latest_ready_run_id = str(ready_runs[0].get("run_id") or "")
                previous_run_id = str(ready_runs[1].get("run_id") or "")
                trend = {
                    "latest_run": {"run_id": ready_runs[0].get("run_id"), "run_ts": ready_runs[0].get("run_ts")},
                    "previous_run": {"run_id": ready_runs[1].get("run_id"), "run_ts": ready_runs[1].get("run_ts")},
                    "findings": _findings_trend(
                        conn,
                        tenant_id=tenant_id,
                        workspace=workspace,
                        latest_run_id=latest_ready_run_id,
                        previous_run_id=previous_run_id,
                    ),
                    "recommendations": _recommendations_trend(
                        conn,
                        tenant_id=tenant_id,
                        workspace=workspace,
                        latest_run_id=latest_ready_run_id,
                        previous_run_id=previous_run_id,
                    ),
                    "coverage": _coverage_trend(
                        conn,
                        tenant_id=tenant_id,
                        workspace=workspace,
                        latest_run_id=latest_ready_run_id,
                        previous_run_id=previous_run_id,
                    ),
                }

        return _json(
            {
                "ok": True,
                "tenant_id": tenant_id,
                "workspace": workspace,
                "latest_run": latest_run,
                "kpis": {
                    "findings": findings,
                    "recommendations": recommendations,
                    "realized": realized,
                    "coverage": coverage,
                },
                "trend": trend,
                "notes": [
                    "KPI families are parallel views of value and trust, not additive components of one total.",
                    "Findings reflect detected open waste signals, recommendations reflect rule-eligible actions, realized reflects tracked remediation outcomes, and coverage reflects assessment completeness.",
                ],
            }
        )
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, status=400)
