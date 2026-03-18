"""Unit tests for shared Flask audit helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from apps.flask_api.audit import AuditEvent, _audit_insert_params


def test_audit_insert_params_serializes_datetime_values() -> None:
    """Audit helper should serialize datetime values in nested payloads."""
    event = AuditEvent(
        tenant_id="acme",
        workspace="prod",
        entity_type="tenant_workspace",
        entity_id="ws-1",
        fingerprint=None,
        event_type="tenant_admin.workspace.updated",
        event_category="tenant_admin",
        previous_value={
            "status": "active",
            "updated_at": datetime(2026, 3, 18, 20, 31, 49, tzinfo=UTC),
        },
        new_value={
            "status": "archived",
            "timestamps": [
                datetime(2026, 3, 18, 21, 31, 49, tzinfo=UTC),
            ],
        },
        actor_id="admin-1",
        actor_email="admin@acme.io",
        actor_name="Admin",
        source="/api/tenant-admin/workspaces/ws-1",
    )

    params = _audit_insert_params(event)
    previous_value = json.loads(str(params[7]))
    new_value = json.loads(str(params[8]))

    assert previous_value["updated_at"] == "2026-03-18T20:31:49+00:00"
    assert new_value["timestamps"] == ["2026-03-18T21:31:49+00:00"]
