"""Flask API entrypoint for McKaySystem.

Core concepts:
- tenant_id + workspace scope every query
- finding_current is the findings read model
- lifecycle actions upsert into finding_state_current
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import threading
import time
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, request

from apps.backend.db import (
    db_conn,
    execute_conn,
    fetch_all_dict_conn,
    fetch_one_dict_conn,
)
from apps.flask_api.blueprints import api_keys as api_keys_module
from apps.flask_api.blueprints import auth as auth_module
from apps.flask_api.blueprints import facets as facets_module
from apps.flask_api.blueprints import findings as findings_module
from apps.flask_api.blueprints import groups as groups_module
from apps.flask_api.blueprints import health as health_module
from apps.flask_api.blueprints import kpis as kpis_module
from apps.flask_api.blueprints import lifecycle as lifecycle_module
from apps.flask_api.blueprints import recommendations as recommendations_module
from apps.flask_api.blueprints import remediations as remediations_module
from apps.flask_api.blueprints import runs as runs_module
from apps.flask_api.blueprints import sla_policies as sla_policies_module
from apps.flask_api.blueprints import tenant_admin as tenant_admin_module
from apps.flask_api.blueprints import teams as teams_module
from apps.flask_api.blueprints import users as users_module
from apps.flask_api import graph_context as graph_context_module
from apps.flask_api.utils import (
    _MISSING,
    _coerce_optional_text,
    _coerce_positive_int,
    _err,
    _json,
    _ok,
    _parse_csv_list,
    _parse_int,
    _parse_iso8601_dt,
    _payload_optional_text,
    _q,
    _require_scope_from_json,
    _require_scope_from_query,
    _safe_scope_from_request,
)
from infra.config import get_settings

app = Flask(__name__)
_LOGGER = logging.getLogger(__name__)
_API_VERSION_RE = re.compile(r"^v(?P<major>\d+)$")
_SETTINGS = get_settings()


def _resolved_api_version() -> str:
    raw = str(_SETTINGS.api.version or "v1").strip().lower()
    if _API_VERSION_RE.match(raw):
        return raw
    return "v1"


_API_VERSION = _resolved_api_version()
_API_PREFIX = f"/api/{_API_VERSION}"
_versioned_aliases_registered = False
_API_ROUTE_SLOS_MS: dict[str, int] = {
    "/api/findings": 500,
    "/api/recommendations": 500,
    "/api/groups": 700,
    "/api/remediations/impact": 800,
}


def _canonical_api_path(path: str) -> str:
    """Normalize versioned API paths to the canonical /api/* form."""
    value = str(path or "")
    if not value.startswith("/api/"):
        return value
    m = re.match(r"^/api/v\d+(?P<rest>/.*|$)", value)
    if not m:
        return value
    rest = m.group("rest") or ""
    if not rest:
        return "/api"
    return f"/api{rest}"


def _rule_to_openapi_path(path: str) -> str:
    """Convert Flask route params to OpenAPI-style path parameters."""
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", str(path or ""))


# Operational hardening.
# Logging and rate limiting stay lightweight here. Prefer reverse-proxy or WAF
# controls in hosted environments.

_API_DEBUG_ERRORS = bool(_SETTINGS.api.debug_errors)
_API_LOG_LEVEL = str(_SETTINGS.api.log_level or "INFO").strip().upper()
_API_CORS_ALLOWED_ORIGINS = tuple(
    str(x).strip() for x in _SETTINGS.api.cors_allowed_origins if str(x).strip()
)
_API_CORS_ALLOW_CREDENTIALS = bool(_SETTINGS.api.cors_allow_credentials)
_API_CORS_ALLOWED_HEADERS = (
    "Content-Type, Authorization, X-Tenant-Id, X-Tenant, X-Workspace, X-WS, "
    "X-Correlation-Id, X-Request-Id"
)
_API_CORS_ALLOWED_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_API_CORS_MAX_AGE_SECONDS = "600"

_RATE_LIMIT_RPS = _SETTINGS.api.rate_limit_rps
_RATE_LIMIT_BURST = _SETTINGS.api.rate_limit_burst


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _log(level: str, event: str, fields: dict[str, Any]) -> None:
    """Emit a single-line JSON log via the standard logging pipeline."""
    level_u = (level or "INFO").upper()
    order = {"ERROR": 3, "WARN": 2, "INFO": 1}
    if order.get(level_u, 1) < order.get(_API_LOG_LEVEL, 1):
        return
    payload: dict[str, Any] = {"ts": _iso_z(_now_utc()), "level": level_u, "event": event}
    payload.update(fields)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if level_u == "ERROR":
        _LOGGER.error("%s", payload_json)
    elif level_u == "WARN":
        _LOGGER.warning("%s", payload_json)
    else:
        _LOGGER.info("%s", payload_json)


def _merge_vary_header(current: str | None, token: str) -> str:
    """Return a Vary header value that includes token exactly once."""
    items = [x.strip() for x in str(current or "").split(",") if x.strip()]
    token_norm = token.strip()
    if token_norm and token_norm.lower() not in {x.lower() for x in items}:
        items.append(token_norm)
    return ", ".join(items)


def _cors_request_origin() -> str | None:
    """Return normalized Origin header value when present."""
    origin = str(request.headers.get("Origin") or "").strip()
    return origin or None


def _cors_origin_allowed(origin: str | None) -> bool:
    """Return True when CORS origin is explicitly allowed by config."""
    if not origin or not _API_CORS_ALLOWED_ORIGINS:
        return False
    if "*" in _API_CORS_ALLOWED_ORIGINS:
        return True
    return origin in _API_CORS_ALLOWED_ORIGINS


def _cors_allow_origin_value(origin: str) -> str:
    """Resolve Access-Control-Allow-Origin response value."""
    if "*" in _API_CORS_ALLOWED_ORIGINS and not _API_CORS_ALLOW_CREDENTIALS:
        return "*"
    return origin


def _apply_cors_headers(resp: Response) -> Response:
    """Attach CORS headers for allowed API origins."""
    path = _canonical_api_path(request.path or "")
    if not path.startswith("/api/"):
        return resp

    origin = _cors_request_origin()
    if not _cors_origin_allowed(origin):
        return resp

    assert origin is not None
    resp.headers["Access-Control-Allow-Origin"] = _cors_allow_origin_value(origin)
    resp.headers["Access-Control-Allow-Methods"] = _API_CORS_ALLOWED_METHODS
    resp.headers["Access-Control-Allow-Headers"] = _API_CORS_ALLOWED_HEADERS
    resp.headers["Access-Control-Max-Age"] = _API_CORS_MAX_AGE_SECONDS
    resp.headers["Vary"] = _merge_vary_header(resp.headers.get("Vary"), "Origin")
    if _API_CORS_ALLOW_CREDENTIALS:
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


@app.before_request
def _start_timer() -> None:
    request.environ["_mckay_t0"] = time.monotonic()


@app.before_request
def _handle_cors_preflight() -> Response | None:
    """Short-circuit API preflight requests for allowed CORS origins."""
    path = _canonical_api_path(request.path or "")
    if request.method != "OPTIONS" or not path.startswith("/api/"):
        return None
    origin = _cors_request_origin()
    if not _cors_origin_allowed(origin):
        return None
    return _apply_cors_headers(Response(status=204))


def _api_route_perf_context(resp: Response, *, ms: int | None) -> dict[str, Any] | None:
    """Return structured perf context for priority API routes only."""
    path = _canonical_api_path(request.path or "")
    slo_ms = _API_ROUTE_SLOS_MS.get(path)
    if slo_ms is None:
        return None

    tenant_id, workspace = _safe_scope_from_request()
    limit = _q("limit")
    offset = _q("offset")
    query_value = _q("q")
    refresh_value = _q("refresh")
    payload: dict[str, Any] = {
        "route_key": path,
        "method": request.method,
        "path": request.path,
        "status": int(getattr(resp, "status_code", 0) or 0),
        "ms": ms,
        "slo_ms": slo_ms,
        "tenant_id": tenant_id,
        "workspace": workspace,
        "limit": None if limit in (None, "") else limit,
        "offset": None if offset in (None, "") else offset,
        "has_q": bool(query_value),
        "refresh": None if refresh_value in (None, "") else refresh_value,
    }

    response_json = resp.get_json(silent=True)
    if isinstance(response_json, dict):
        items = response_json.get("items")
        payload["items_count"] = len(items) if isinstance(items, list) else None
        total = response_json.get("total")
        payload["total"] = total if isinstance(total, int) else total
        refreshed = response_json.get("refreshed")
        payload["refreshed"] = refreshed if isinstance(refreshed, int) else None
    else:
        payload["items_count"] = None
        payload["total"] = None
        payload["refreshed"] = None

    return payload


@app.after_request
def _log_request(resp: Response) -> Response:
    try:
        t0 = float(request.environ.get("_mckay_t0") or 0.0)
        ms = int(max(0.0, (time.monotonic() - t0) * 1000.0)) if t0 else None
        tenant_id, workspace = _safe_scope_from_request()
        _log(
            "INFO",
            "http_request",
            {
                "method": request.method,
                "path": request.path,
                "status": int(getattr(resp, "status_code", 0) or 0),
                "ms": ms,
                "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
                "ua": request.headers.get("User-Agent", ""),
                "tenant_id": tenant_id,
                "workspace": workspace,
            },
        )
        perf_context = _api_route_perf_context(resp, ms=ms)
        if perf_context is not None:
            _log("INFO", "api_route_perf", perf_context)
            slo_ms = int(perf_context.get("slo_ms") or 0)
            if ms is not None and slo_ms > 0 and ms > slo_ms:
                _log("WARN", "api_route_slo_breach", perf_context)
    except (RuntimeError, TypeError, ValueError) as exc:
        _LOGGER.debug("request logging skipped: %s", exc)

    # Findings/lifecycle data must never be served stale from intermediary caches.
    path = request.path or ""
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers["Vary"] = _merge_vary_header(resp.headers.get("Vary"), "Authorization")
    return _apply_cors_headers(resp)


class _TokenBucket:
    __slots__ = ("capacity", "tokens", "fill_rate", "last")

    def __init__(self, capacity: float, fill_rate: float) -> None:
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.fill_rate = float(fill_rate)
        self.last = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self.last)
        self.last = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


_rate_lock = threading.Lock()
_rate_buckets: dict[str, _TokenBucket] = {}
_schema_gate_lock = threading.Lock()
_schema_gate_checked = False
_schema_gate_enabled = bool(_SETTINGS.api.enforce_schema_gate)


def _rate_limits() -> tuple[float | None, float | None]:
    if _RATE_LIMIT_RPS is None:
        return None, None
    rps = float(_RATE_LIMIT_RPS)
    if rps <= 0:
        return None, None

    burst: float | None = float(_RATE_LIMIT_BURST) if _RATE_LIMIT_BURST is not None else None
    if burst is None:
        burst = max(10.0, rps * 2.0)
    return rps, burst


def _rate_key() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    path = _canonical_api_path(request.path or "")
    if path.startswith("/api/lifecycle/"):
        group = "/api/lifecycle"
    elif path.startswith("/api/findings"):
        group = "/api/findings"
    elif path.startswith("/api/recommendations"):
        group = "/api/recommendations"
    elif path.startswith("/api/groups"):
        group = "/api/groups"
    elif path.startswith("/api/runs"):
        group = "/api/runs"
    else:
        group = "/api/other"
    return f"{ip}|{group}"


@app.before_request
def _enforce_rate_limit() -> None:
    path = _canonical_api_path(request.path or "")
    if not path.startswith("/api/"):
        return
    if path in {"/api/health/db"}:
        return

    rps, burst = _rate_limits()
    if rps is None or burst is None:
        return

    key = _rate_key()
    with _rate_lock:
        bucket = _rate_buckets.get(key)
        if bucket is None:
            bucket = _TokenBucket(capacity=burst, fill_rate=rps)
            _rate_buckets[key] = bucket
        allowed = bucket.allow(1.0)

    if not allowed:
        _log("WARN", "rate_limited", {"key": key, "path": path})
        abort(429)


def _api_internal_error_response(exc: Exception) -> Any:
    """Map API internal errors to stable, route-specific response shapes."""
    path = _canonical_api_path(request.path or "")
    exc_text = str(exc)

    if path == "/api/health/db":
        if _API_DEBUG_ERRORS:
            return _err("db_unhealthy", "db health check failed", status=500, extra={"detail": exc_text})
        return _err("db_unhealthy", "db health check failed", status=500)

    if path == "/api/findings":
        extra = None
        if _API_DEBUG_ERRORS:
            extra = {"detail": exc_text, "traceback": traceback.format_exc()}
        return _err("internal_error", "internal error", status=500, extra=extra)

    if path in {
        "/api/findings/aggregates",
        "/api/facets",
        "/api/lifecycle/group/ignore",
        "/api/lifecycle/group/resolve",
        "/api/lifecycle/group/snooze",
        "/api/lifecycle/ignore",
        "/api/lifecycle/resolve",
        "/api/lifecycle/snooze",
    }:
        return _err(
            "internal_error",
            "internal error",
            status=500,
            extra={"detail": exc_text},
        )

    if path in {"/api/runs/diff/latest", "/api/groups"} or path.startswith("/api/groups/"):
        return _err(
            "internal_error",
            "internal error",
            status=500,
            extra={"detail": exc_text},
        )

    return _err("internal_error", "internal error", status=500)


def _schema_migrations_dir() -> Path:
    """Return the repository-local migrations directory used by schema gate."""
    return Path(__file__).resolve().parents[2] / "migrations"


def _ensure_schema_gate() -> None:
    """Run DB schema gate once per process."""
    global _schema_gate_checked
    if not _schema_gate_enabled or _schema_gate_checked:
        return
    with _schema_gate_lock:
        if _schema_gate_checked:
            return
        from apps.backend.db_migrate import ensure_schema_current

        ensure_schema_current(migrations_dir=_schema_migrations_dir())
        _schema_gate_checked = True


@app.before_request
def _enforce_schema_gate() -> Any | None:
    """Return 503 if the DB schema is behind local code migrations."""
    path = _canonical_api_path(request.path or "")
    if not path.startswith("/api/"):
        return None
    if path in {"/api/health/db"}:
        return None
    try:
        _ensure_schema_gate()
    except RuntimeError as exc:
        _log("ERROR", "schema_gate_failed", {"detail": str(exc)})
        return _err("schema_mismatch", str(exc), status=503)
    return None


@app.errorhandler(429)
def _err_429(_: Exception) -> Any:
    return _err("rate_limited", "too many requests", status=429)


@app.errorhandler(500)
def _err_500(exc: Exception) -> Any:
    root_exc = getattr(exc, "original_exception", None) or exc
    tb = traceback.format_exc()
    fields: dict[str, Any] = {"path": request.path, "detail": str(root_exc)}
    if _API_DEBUG_ERRORS:
        fields["traceback"] = tb
    _log("ERROR", "unhandled_exception", fields)
    return _api_internal_error_response(root_exc)

# --------------------
# Auth (Bearer token)
# --------------------

# If API_BEARER_TOKEN is unset/empty, authentication is disabled (useful for
# local dev). In hosted environments, set it to require:
#   Authorization: Bearer <token>
_API_BEARER_TOKEN = str(_SETTINGS.api.bearer_token or "").strip()


def _is_auth_required() -> bool:
    return bool(_API_BEARER_TOKEN)


def _check_bearer_token() -> None:
    """Abort the request if the bearer token is missing/invalid."""
    if not _is_auth_required():
        return

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401)

    token = auth[len("Bearer ") :].strip()
    # constant-time comparison
    if not hmac.compare_digest(token, _API_BEARER_TOKEN):
        abort(403)


@app.before_request
def _enforce_api_auth() -> None:
    """Enforce bearer auth for API routes.

    - /health remains public.
    - /api/health/db remains public (useful for platform health checks).
    - /api/auth/*, /api/users/*, /api/api-keys/* enforce RBAC via auth middleware.
    - All other /api/* routes require Authorization: Bearer ... when
      API_BEARER_TOKEN is set.
    """
    path = _canonical_api_path(request.path or "")
    if not path.startswith("/api/"):
        return
    if request.method == "OPTIONS":
        return
    if path in {"/api/health/db", "/api/auth/login"}:
        return
    if path == "/api/users" or path.startswith("/api/users/"):
        return
    if path == "/api/api-keys" or path.startswith("/api/api-keys/"):
        return
    if path.startswith("/api/auth/"):
        return
    _check_bearer_token()


# --------------------
# OpenAPI + API versioning
# --------------------

_OPENAPI_EXCLUDED_CANONICAL_PATHS = {"/api/openapi.json"}


def _operation_summary_from_view(view_func: Any, method: str, path: str) -> str:
    doc = str(getattr(view_func, "__doc__", "") or "").strip()
    if doc:
        return doc.splitlines()[0].strip()
    clean = path.strip("/").replace("/", " ")
    return f"{method.upper()} {clean or 'root'}"


def _openapi_security_for_path(path: str) -> list[dict[str, list[str]]]:
    # Keep health + OpenAPI discovery unauthenticated in docs.
    if path in {"/api/health/db", "/api/openapi.json", "/api/auth/login"}:
        return []
    return [{"bearerAuth": []}]


def _build_openapi_spec() -> dict[str, Any]:
    """Build OpenAPI 3.0 spec from registered Flask API routes."""
    paths: dict[str, dict[str, Any]] = {}
    seen_ops: set[tuple[str, str]] = set()

    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue

        canonical = _canonical_api_path(str(rule.rule or ""))
        if not canonical.startswith("/api/"):
            continue
        if canonical in _OPENAPI_EXCLUDED_CANONICAL_PATHS:
            continue

        subpath = canonical[len("/api") :] or "/"
        openapi_path = _rule_to_openapi_path(subpath)

        raw_methods = set(rule.methods or set())
        methods = sorted(m.lower() for m in raw_methods if m not in {"HEAD", "OPTIONS"})
        if not methods:
            continue

        view_func = app.view_functions.get(rule.endpoint)
        if view_func is None:
            continue

        path_item = paths.setdefault(openapi_path, {})
        for method in methods:
            op_key = (openapi_path, method)
            if op_key in seen_ops:
                continue
            seen_ops.add(op_key)

            method_u = method.upper()
            parameters: list[dict[str, Any]] = []
            for arg in sorted(rule.arguments):
                parameters.append(
                    {
                        "name": arg,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                )

            operation: dict[str, Any] = {
                "operationId": f"{rule.endpoint.replace('.', '_')}_{method}",
                "summary": _operation_summary_from_view(view_func, method_u, openapi_path),
                "tags": [openapi_path.strip("/").split("/", 1)[0] or "root"],
                "parameters": parameters,
                "responses": {
                    "200": {"description": "Successful response"},
                    "400": {"description": "Bad request"},
                    "500": {"description": "Internal server error"},
                },
                "security": _openapi_security_for_path(canonical),
            }
            if method in {"post", "put", "patch"}:
                operation["requestBody"] = {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                        }
                    },
                }
            path_item[method] = operation

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "McKaySystem API",
            "version": _API_VERSION,
            "description": "Generated from Flask routes. Both versioned and compatibility API bases are exposed.",
        },
        "servers": [
            {"url": _API_PREFIX, "description": f"Versioned API base ({_API_VERSION})"},
            {"url": "/api", "description": "Compatibility API base"},
        ],
        "paths": dict(sorted(paths.items(), key=lambda kv: kv[0])),
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "token",
                }
            }
        },
    }


@app.get("/openapi.json")
def api_openapi_public() -> Any:
    """Return the public OpenAPI document."""
    return _json(_build_openapi_spec())


@app.get("/api/openapi.json")
def api_openapi_scoped() -> Any:
    """Return the OpenAPI document under the API base."""
    return _json(_build_openapi_spec())


@app.get("/api/version")
def api_version() -> Any:
    """Return API version metadata."""
    return _json(
        {
            "version": _API_VERSION,
            "prefix": _API_PREFIX,
            "supported_versions": [_API_VERSION],
            "legacy_prefix": "/api",
        }
    )


def _now_utc() -> datetime:
    return datetime.now(UTC)

def _register_versioned_api_aliases() -> None:
    """Register `/api/<version>/...` aliases for all existing `/api/...` routes."""
    global _versioned_aliases_registered
    if _versioned_aliases_registered:
        return

    existing_rules = {str(rule.rule or "") for rule in app.url_map.iter_rules()}
    rules = list(app.url_map.iter_rules())
    for rule in rules:
        raw_path = str(rule.rule or "")
        if not raw_path.startswith("/api/"):
            continue
        if re.match(r"^/api/v\d+(?:/|$)", raw_path):
            continue

        versioned_path = f"{_API_PREFIX}{raw_path[len('/api'):]}"
        if versioned_path in existing_rules:
            continue

        view_func = app.view_functions.get(rule.endpoint)
        if view_func is None:
            continue

        methods = sorted(m for m in (rule.methods or set()) if m not in {"HEAD", "OPTIONS"})
        if not methods:
            continue

        alias_endpoint = f"{rule.endpoint}__{_API_VERSION}"
        app.add_url_rule(
            versioned_path,
            endpoint=alias_endpoint,
            view_func=view_func,
            methods=methods,
            strict_slashes=rule.strict_slashes,
        )
        existing_rules.add(versioned_path)

    _versioned_aliases_registered = True


# Compatibility symbols expected by older tests that monkeypatch flask_app.
_team_exists = teams_module._team_exists
_fetch_team_member = teams_module._fetch_team_member
_fetch_sla_policy_category = sla_policies_module._fetch_sla_policy_category
_fetch_sla_policy_override = sla_policies_module._fetch_sla_policy_override
_audit_log_event = findings_module._audit_log_event
_upsert_state = lifecycle_module._upsert_state
_audit_lifecycle = lifecycle_module._audit_lifecycle
_finding_exists = findings_module._finding_exists
_ensure_finding_governance_row = findings_module._ensure_finding_governance_row
_fetch_governance_owner_team = findings_module._fetch_governance_owner_team
_update_finding_owner = findings_module._update_finding_owner
_update_finding_team = findings_module._update_finding_team
_fetch_finding_effective_state = findings_module._fetch_finding_effective_state
_fetch_governance_sla = findings_module._fetch_governance_sla
_apply_finding_sla_extension = findings_module._apply_finding_sla_extension


def _install_blueprint_backcompat_shims() -> None:
    """Bind shared symbols onto blueprint modules for test monkeypatching."""

    def _db_conn_proxy() -> Any:
        return db_conn()

    def _fetch_all_proxy(conn: Any, sql: str, params: Iterable[Any] | None = None) -> Any:
        return fetch_all_dict_conn(conn, sql, params)

    def _fetch_one_proxy(conn: Any, sql: str, params: Iterable[Any] | None = None) -> Any:
        return fetch_one_dict_conn(conn, sql, params)

    def _execute_proxy(conn: Any, sql: str, params: Iterable[Any] | None = None) -> Any:
        return execute_conn(conn, sql, params)

    for module in (
        health_module,
        auth_module,
        users_module,
        api_keys_module,
        runs_module,
        kpis_module,
        findings_module,
        recommendations_module,
        remediations_module,
        teams_module,
        tenant_admin_module,
        sla_policies_module,
        lifecycle_module,
        groups_module,
        facets_module,
        graph_context_module,
    ):
        if hasattr(module, "db_conn"):
            module.db_conn = _db_conn_proxy
        if hasattr(module, "fetch_all_dict_conn"):
            module.fetch_all_dict_conn = _fetch_all_proxy
        if hasattr(module, "fetch_one_dict_conn"):
            module.fetch_one_dict_conn = _fetch_one_proxy
        if hasattr(module, "execute_conn"):
            module.execute_conn = _execute_proxy

    teams_module._team_exists = lambda *args, **kwargs: _team_exists(*args, **kwargs)
    teams_module._fetch_team_member = lambda *args, **kwargs: _fetch_team_member(*args, **kwargs)
    teams_module._audit_log_event = lambda *args, **kwargs: _audit_log_event(*args, **kwargs)

    sla_policies_module._fetch_sla_policy_category = (
        lambda *args, **kwargs: _fetch_sla_policy_category(*args, **kwargs)
    )
    sla_policies_module._fetch_sla_policy_override = (
        lambda *args, **kwargs: _fetch_sla_policy_override(*args, **kwargs)
    )
    sla_policies_module._audit_log_event = lambda *args, **kwargs: _audit_log_event(*args, **kwargs)

    lifecycle_module._upsert_state = lambda *args, **kwargs: _upsert_state(*args, **kwargs)
    lifecycle_module._finding_exists = lambda *args, **kwargs: _finding_exists(*args, **kwargs)
    lifecycle_module._audit_log_event = lambda *args, **kwargs: _audit_log_event(*args, **kwargs)
    lifecycle_module._audit_lifecycle = lambda *args, **kwargs: _audit_lifecycle(*args, **kwargs)

    findings_module._finding_exists = lambda *args, **kwargs: _finding_exists(*args, **kwargs)
    findings_module._team_exists = lambda *args, **kwargs: _team_exists(*args, **kwargs)
    findings_module._ensure_finding_governance_row = (
        lambda *args, **kwargs: _ensure_finding_governance_row(*args, **kwargs)
    )
    findings_module._fetch_governance_owner_team = (
        lambda *args, **kwargs: _fetch_governance_owner_team(*args, **kwargs)
    )
    findings_module._update_finding_owner = lambda *args, **kwargs: _update_finding_owner(*args, **kwargs)
    findings_module._update_finding_team = lambda *args, **kwargs: _update_finding_team(*args, **kwargs)
    findings_module._fetch_finding_effective_state = (
        lambda *args, **kwargs: _fetch_finding_effective_state(*args, **kwargs)
    )
    findings_module._fetch_governance_sla = lambda *args, **kwargs: _fetch_governance_sla(*args, **kwargs)
    findings_module._apply_finding_sla_extension = (
        lambda *args, **kwargs: _apply_finding_sla_extension(*args, **kwargs)
    )
    findings_module._audit_log_event = lambda *args, **kwargs: _audit_log_event(*args, **kwargs)


_install_blueprint_backcompat_shims()

# Register blueprints - each handles its own route definitions.
app.register_blueprint(health_module.health_bp)
app.register_blueprint(auth_module.auth_bp)
app.register_blueprint(users_module.users_bp)
app.register_blueprint(api_keys_module.api_keys_bp)
app.register_blueprint(runs_module.runs_bp)
app.register_blueprint(kpis_module.kpis_bp)
app.register_blueprint(findings_module.findings_bp)
app.register_blueprint(recommendations_module.recommendations_bp)
app.register_blueprint(remediations_module.remediations_bp)
app.register_blueprint(teams_module.teams_bp)
app.register_blueprint(tenant_admin_module.tenant_admin_bp)
app.register_blueprint(sla_policies_module.sla_policies_bp)
app.register_blueprint(lifecycle_module.lifecycle_bp)
app.register_blueprint(groups_module.groups_bp)
app.register_blueprint(facets_module.facets_bp)

# Register versioned aliases after all routes (including blueprints) exist.
_register_versioned_api_aliases()


if __name__ == "__main__":
    app.run(host=str(_SETTINGS.api.host), port=int(_SETTINGS.api.port))
