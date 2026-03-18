"""Shared audit log helpers for Flask API handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

try:
    from flask import request
except ImportError:  # pragma: no cover
    request = None

if TYPE_CHECKING:
    from psycopg2 import Error as PsycopgError  # type: ignore
else:
    try:
        from psycopg2 import Error as _PsycopgError  # type: ignore
    except ImportError:  # pragma: no cover
        class _PsycopgError(Exception):
            """Fallback psycopg error type when psycopg2 import is unavailable."""

    PsycopgError = _PsycopgError


# Dataclass intentionally mirrors audit_log schema columns.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class AuditEvent:
    """Append-only audit log payload."""

    tenant_id: str
    workspace: str
    entity_type: str
    entity_id: str
    event_type: str
    event_category: str
    previous_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    actor_id: str | None
    actor_email: str | None
    actor_name: str | None
    source: str
    run_id: str | None = None
    correlation_id: str | None = None
    fingerprint: str | None = None


# pylint: enable=too-many-instance-attributes


def _audit_json_default(value: Any) -> Any:
    """Return a deterministic JSON-safe representation for audit payload values.

    Args:
        value: Raw value attached to an audit event payload.

    Returns:
        A JSON-serializable representation of the value.

    Raises:
        TypeError: If the value cannot be serialized safely.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _audit_insert_params(event: AuditEvent) -> tuple[Any, ...]:
    """Build SQL parameter tuple for one audit event."""
    try:
        if request is None:
            raise RuntimeError("flask request unavailable")
        ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
        user_agent = request.headers.get("User-Agent", "")
    except RuntimeError:
        ip_address = None
        user_agent = ""

    return (
        event.tenant_id,
        event.workspace,
        event.entity_type,
        event.entity_id,
        event.fingerprint,
        event.event_type,
        event.event_category,
        (
            json.dumps(
                event.previous_value,
                separators=(",", ":"),
                default=_audit_json_default,
            )
            if event.previous_value is not None
            else None
        ),
        (
            json.dumps(
                event.new_value,
                separators=(",", ":"),
                default=_audit_json_default,
            )
            if event.new_value is not None
            else None
        ),
        event.actor_id,
        event.actor_email,
        event.actor_name,
        event.source,
        ip_address,
        user_agent,
        event.run_id,
        event.correlation_id,
    )


def append_audit_event(conn: Any, *, event: AuditEvent) -> None:
    """Append an audit event without breaking the caller on audit failure."""
    cursor_factory = getattr(conn, "cursor", None)
    if not callable(cursor_factory):
        return

    with conn.cursor() as cur:
        try:
            cur.execute("SAVEPOINT mckay_audit_log_1")
            cur.execute(
                """
                INSERT INTO audit_log
                  (tenant_id, workspace, entity_type, entity_id, fingerprint,
                   event_type, event_category, previous_value, new_value,
                   actor_id, actor_email, actor_name, source,
                   ip_address, user_agent, run_id, correlation_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                _audit_insert_params(event),
            )
            cur.execute("RELEASE SAVEPOINT mckay_audit_log_1")
        except PsycopgError:
            cur.execute("ROLLBACK TO SAVEPOINT mckay_audit_log_1")
