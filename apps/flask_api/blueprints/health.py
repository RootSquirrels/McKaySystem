"""Health endpoints."""

from typing import Any

from flask import Blueprint

from apps.backend.db import db_conn, fetch_one_dict_conn
from apps.flask_api.utils import _ok

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health() -> Any:
    """Return a lightweight liveness response."""
    return _ok()


@health_bp.route("/api/health/db", methods=["GET"])
def api_health_db() -> Any:
    """Return database connectivity status."""
    with db_conn() as conn:
        row = fetch_one_dict_conn(conn, "SELECT 1 AS ok")
    return _ok({"db": bool(row and row.get("ok") == 1)})
