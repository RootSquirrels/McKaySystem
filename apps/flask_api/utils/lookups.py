"""Shared scoped lookup helpers for Flask API handlers."""

from typing import Any

from apps.backend.db import fetch_one_dict_conn


def finding_exists(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    fingerprint: str,
) -> bool:
    """Return whether a finding exists in tenant/workspace scope."""
    row = fetch_one_dict_conn(
        conn,
        """
        SELECT 1 AS ok
        FROM finding_latest
        WHERE tenant_id = %s AND workspace = %s AND fingerprint = %s
        LIMIT 1
        """,
        (tenant_id, workspace, fingerprint),
    )
    return bool(row and row.get("ok") == 1)


def team_exists(
    conn: Any,
    *,
    tenant_id: str,
    workspace: str,
    team_id: str,
) -> bool:
    """Return whether a team exists in tenant/workspace scope."""
    row = fetch_one_dict_conn(
        conn,
        """
        SELECT 1 AS ok
        FROM teams
        WHERE tenant_id = %s AND workspace = %s AND team_id = %s
        LIMIT 1
        """,
        (tenant_id, workspace, team_id),
    )
    return bool(row and row.get("ok") == 1)
