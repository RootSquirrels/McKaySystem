"""Unit tests for RBAC database helper query contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from apps.backend import db_rbac


class _FakeCursor:
    """Cursor test double for write helpers."""

    def __init__(
        self,
        *,
        row: Sequence[Any] | None = None,
        description: Sequence[Sequence[Any]] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = row
        self.description = description or []
        self.rowcount = rowcount
        self.executed_sql: str | None = None
        self.executed_params: Sequence[Any] | None = None
        self.executed_statements: list[tuple[str, Sequence[Any] | None]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:  # type: ignore[no-untyped-def]
        return False

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        self.executed_sql = sql
        self.executed_params = params
        self.executed_statements.append((sql, params))

    def fetchone(self) -> Sequence[Any] | None:
        return self._row


class _FakeConn:
    """Connection test double that exposes one cursor instance."""

    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        return


def test_get_user_by_email_includes_tenant_and_workspace(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured["sql"] = sql
        captured["params"] = params
        return {"user_id": "user-1"}

    monkeypatch.setattr(db_rbac, "fetch_one_dict_conn", _fake_fetch_one)
    row = db_rbac.get_user_by_email(
        object(),
        tenant_id="acme",
        workspace="prod",
        email="alice@acme.io",
    )

    assert row == {"user_id": "user-1"}
    assert captured["params"] == ("acme", "prod", "alice@acme.io")
    sql = str(captured["sql"]).lower()
    assert "from users" in sql
    assert "tenant_id = %s" in sql
    assert "workspace = %s" in sql


def test_create_user_is_idempotent_and_scoped() -> None:
    cursor = _FakeCursor(
        row=("acme", "prod", "user-1"),
        description=(("tenant_id",), ("workspace",), ("user_id",)),
    )
    conn = _FakeConn(cursor)

    row = db_rbac.create_user(
        conn,
        user=db_rbac.UserUpsert(
            tenant_id="acme",
            workspace="prod",
            user_id="user-1",
            email="alice@acme.io",
            password_hash="hash",
        ),
    )

    assert row == {"tenant_id": "acme", "workspace": "prod", "user_id": "user-1"}
    assert cursor.executed_params is not None
    assert tuple(cursor.executed_params)[0:3] == ("acme", "prod", "user-1")
    sql = str(cursor.executed_sql).lower()
    assert "insert into users" in sql
    assert "on conflict (tenant_id, workspace, user_id)" in sql


def test_list_users_page_applies_scope_and_count(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["items_sql"] = sql
        captured["items_params"] = params
        return [{"user_id": "u-1"}]

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured["count_sql"] = sql
        captured["count_params"] = params
        return {"n": 1}

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(db_rbac, "fetch_one_dict_conn", _fake_fetch_one)

    rows, total = db_rbac.list_users_page(
        object(),
        query=db_rbac.UserListQuery(
            tenant_id="acme",
            workspace="prod",
            limit=50,
            offset=10,
            query="alice",
            include_inactive=False,
        ),
    )

    assert rows == [{"user_id": "u-1"}]
    assert total == 1
    assert captured["items_params"] == ("acme", "prod", "%alice%", "%alice%", "%alice%", 50, 10)
    assert captured["count_params"] == ("acme", "prod", "%alice%", "%alice%", "%alice%")
    assert "from users" in str(captured["items_sql"]).lower()
    assert "count(*)::bigint" in str(captured["count_sql"]).lower()


def test_list_tenant_user_directory_applies_scope_and_count(monkeypatch: Any) -> None:
    """Tenant user directory should apply tenant anchor scope and aggregate paging."""
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["items_sql"] = sql
        captured["items_params"] = params
        return [{"user_id": "u-1", "email": "u-1@acme.io"}]

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured["count_sql"] = sql
        captured["count_params"] = params
        return {"n": 1}

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)
    monkeypatch.setattr(db_rbac, "fetch_one_dict_conn", _fake_fetch_one)

    rows, total = db_rbac.list_tenant_user_directory(
        object(),
        query=db_rbac.TenantUserDirectoryQuery(
            tenant_id="acme",
            anchor_workspace="prod",
            limit=20,
            offset=0,
            query="alice",
            include_inactive=False,
        ),
    )

    assert rows == [{"user_id": "u-1", "email": "u-1@acme.io"}]
    assert total == 1
    assert captured["items_params"] == (
        "acme",
        "%alice%",
        "%alice%",
        "%alice%",
        "acme",
        "prod",
        "acme",
        "prod",
        db_rbac.TENANT_POLICY_WORKSPACE,
        20,
        0,
    )
    assert captured["count_params"] == (
        "acme",
        "%alice%",
        "%alice%",
        "%alice%",
        "acme",
        "prod",
        "acme",
        "prod",
        db_rbac.TENANT_POLICY_WORKSPACE,
    )
    assert "from users u" in str(captured["items_sql"]).lower()
    assert "count(*)::bigint" in str(captured["count_sql"]).lower()


def test_list_roles_applies_scope_and_returns_permissions(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "tenant_id": "acme",
                "workspace": "prod",
                "role_id": "viewer",
                "name": "Viewer",
                "description": "Read-only",
                "is_system": True,
                "permissions": ["findings:read", "runs:read"],
            }
        ]

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)

    rows = db_rbac.list_roles(
        object(),
        tenant_id="acme",
        workspace="prod",
    )

    assert rows[0]["role_id"] == "viewer"
    assert rows[0]["permissions"] == ["findings:read", "runs:read"]
    assert captured["params"] == ("acme", "prod")
    sql = str(captured["sql"]).lower()
    assert "from roles r" in sql
    assert "left join role_permissions" in sql


def test_list_api_keys_applies_scope_and_active_filter(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)
    rows = db_rbac.list_api_keys(
        object(),
        tenant_id="acme",
        workspace="prod",
        user_id="user-1",
        include_inactive=False,
    )

    assert rows == []
    assert captured["params"] == ("acme", "prod", "user-1")
    sql = str(captured["sql"]).lower()
    assert "from api_keys" in sql
    assert "tenant_id = %s" in sql
    assert "workspace = %s" in sql
    assert "is_active = true" in sql


def test_set_user_active_updates_scoped_user() -> None:
    cursor = _FakeCursor(rowcount=1)
    conn = _FakeConn(cursor)

    changed = db_rbac.set_user_active(
        conn,
        tenant_id="acme",
        workspace="prod",
        user_id="user-1",
        is_active=False,
    )

    assert changed is True
    assert cursor.executed_params == (False, "acme", "prod", "user-1")
    sql = str(cursor.executed_sql).lower()
    assert "update users" in sql
    assert "tenant_id = %s" in sql
    assert "workspace = %s" in sql


def test_revoke_api_key_returns_false_when_no_rows_updated() -> None:
    cursor = _FakeCursor(rowcount=0)
    conn = _FakeConn(cursor)

    changed = db_rbac.revoke_api_key(
        conn,
        tenant_id="acme",
        workspace="prod",
        key_id="key-1",
    )

    assert changed is False
    assert cursor.executed_params == ("acme", "prod", "key-1")
    sql = str(cursor.executed_sql).lower()
    assert "update api_keys" in sql
    assert "tenant_id = %s" in sql
    assert "workspace = %s" in sql


def test_check_permission_uses_scoped_join(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        db_rbac,
        "get_user_permissions",
        lambda _conn, **kwargs: captured.update(kwargs) or ["findings:read"],
    )
    allowed = db_rbac.check_permission(
        object(),
        tenant_id="acme",
        workspace="prod",
        user_id="user-1",
        permission_id="findings:read",
    )

    assert allowed is True
    assert captured == {
        "tenant_id": "acme",
        "workspace": "prod",
        "user_id": "user-1",
    }


def test_get_role_by_id_includes_scope(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_one(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any]:
        captured["sql"] = sql
        captured["params"] = params
        return {"role_id": "viewer"}

    monkeypatch.setattr(db_rbac, "fetch_one_dict_conn", _fake_fetch_one)
    row = db_rbac.get_role_by_id(
        object(),
        tenant_id="acme",
        workspace="prod",
        role_id="viewer",
    )

    assert row == {"role_id": "viewer"}
    assert captured["params"] == ("acme", "prod", "viewer")
    sql = str(captured["sql"]).lower()
    assert "from roles" in sql
    assert "tenant_id = %s" in sql
    assert "workspace = %s" in sql


def test_list_tenant_workspaces_uses_anchor_scope(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["sql"] = sql
        captured["params"] = params
        return [{"workspace": "dev"}, {"workspace": "prod"}]

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)
    rows = db_rbac.list_tenant_workspaces(
        object(),
        tenant_id="acme",
        anchor_workspace="prod",
    )

    assert rows == ["dev", "prod"]
    assert captured["params"] == ("acme", "acme", "prod")
    sql = str(captured["sql"]).lower()
    assert "from tenant_workspaces tw" in sql
    assert "from tenant_workspaces anchor" in sql


def test_upsert_user_workspace_role_is_idempotent_and_scoped() -> None:
    cursor = _FakeCursor(
        row=("acme", "prod", "u-1", "editor"),
        description=(("tenant_id",), ("workspace",), ("user_id",), ("role_id",)),
    )
    conn = _FakeConn(cursor)

    row = db_rbac.upsert_user_workspace_role(
        conn,
        assignment=db_rbac.UserWorkspaceRoleUpsert(
            tenant_id="acme",
            workspace="prod",
            user_id="u-1",
            role_id="editor",
            granted_by="admin@acme.io",
        ),
    )

    assert row == {
        "tenant_id": "acme",
        "workspace": "prod",
        "user_id": "u-1",
        "role_id": "editor",
    }
    assert cursor.executed_params == ("acme", "prod", "u-1", "editor", "admin@acme.io")
    sql = str(cursor.executed_sql).lower()
    assert "insert into user_workspace_roles" in sql
    assert "on conflict (tenant_id, workspace, user_id)" in sql


def test_list_registered_tenant_workspaces_uses_anchor_scope(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["sql"] = sql
        captured["params"] = params
        return [{"workspace": "dev"}, {"workspace": "prod"}]

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)

    rows = db_rbac.list_registered_tenant_workspaces(
        object(),
        tenant_id="acme",
        anchor_workspace="prod",
    )

    assert rows == [{"workspace": "dev"}, {"workspace": "prod"}]
    assert captured["params"] == ("acme", "acme", "prod")
    sql = str(captured["sql"]).lower()
    assert "from tenant_workspaces tw" in sql
    assert "from tenant_workspaces anchor" in sql


def test_upsert_tenant_workspace_is_idempotent_and_scoped() -> None:
    cursor = _FakeCursor(
        row=("acme", "prod", "Production"),
        description=(("tenant_id",), ("workspace",), ("display_name",)),
    )
    conn = _FakeConn(cursor)

    row = db_rbac.upsert_tenant_workspace(
        conn,
        workspace_entry=db_rbac.TenantWorkspaceUpsert(
            tenant_id="acme",
            workspace="prod",
            display_name="Production",
            provider="aws",
            scope_kind="account",
            status="active",
            updated_by="admin@acme.io",
        ),
    )

    assert row == {
        "tenant_id": "acme",
        "workspace": "prod",
        "display_name": "Production",
    }
    assert cursor.executed_params is not None
    assert tuple(cursor.executed_params)[0:2] == ("acme", "prod")
    sql = str(cursor.executed_sql).lower()
    assert "insert into tenant_workspaces" in sql
    assert "on conflict (tenant_id, workspace)" in sql


def test_upsert_tenant_role_binding_is_idempotent_and_scoped() -> None:
    cursor = _FakeCursor(
        row=("acme", db_rbac.TENANT_POLICY_WORKSPACE, "u-1", "admin"),
        description=(("tenant_id",), ("workspace",), ("user_id",), ("role_id",)),
    )
    conn = _FakeConn(cursor)

    row = db_rbac.upsert_tenant_role_binding(
        conn,
        binding=db_rbac.TenantRoleBindingUpsert(
            tenant_id="acme",
            user_id="u-1",
            role_id="admin",
            source_workspace="prod",
            granted_by="admin@acme.io",
        ),
    )

    assert row == {
        "tenant_id": "acme",
        "workspace": db_rbac.TENANT_POLICY_WORKSPACE,
        "user_id": "u-1",
        "role_id": "admin",
    }
    assert cursor.executed_params == (
        "acme",
        db_rbac.TENANT_POLICY_WORKSPACE,
        "u-1",
        "admin",
        "prod",
        True,
        "admin@acme.io",
    )
    sql = str(cursor.executed_sql).lower()
    assert "insert into tenant_role_bindings" in sql
    assert "on conflict (tenant_id, workspace, user_id)" in sql


def test_list_inherited_tenant_access_bindings_for_source_workspace_is_scoped(
    monkeypatch: Any,
) -> None:
    """Source-workspace binding lookup should keep tenant and anchor scoping."""
    captured: dict[str, Any] = {}

    def _fake_fetch_all(
        _conn: object, sql: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        captured["sql"] = sql
        captured["params"] = params
        return [{"user_id": "u-1"}]

    monkeypatch.setattr(db_rbac, "fetch_all_dict_conn", _fake_fetch_all)

    row = db_rbac.list_inherited_tenant_access_bindings_for_source_workspace(
        object(),
        tenant_id="acme",
        anchor_workspace="prod",
        source_workspace="sandbox",
    )

    assert row == [{"user_id": "u-1"}]
    assert captured["params"] == (
        "acme",
        db_rbac.TENANT_POLICY_WORKSPACE,
        "sandbox",
        "acme",
        "prod",
        "acme",
        "prod",
    )
    sql = str(captured["sql"]).lower()
    assert "from tenant_role_bindings" in sql
    assert "source_workspace = %s" in sql


def test_bootstrap_rbac_scope_copies_template_scope_idempotently() -> None:
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)

    db_rbac.bootstrap_rbac_scope(
        conn,
        tenant_id="acme",
        workspace="prod",
    )

    assert len(cursor.executed_statements) == 3
    first_sql, first_params = cursor.executed_statements[0]
    second_sql, second_params = cursor.executed_statements[1]
    third_sql, third_params = cursor.executed_statements[2]

    assert first_params == ("acme", "prod", "default", "default")
    assert second_params == ("acme", "prod", "default", "default")
    assert third_params == ("acme", "prod", "default", "default")

    assert "insert into roles" in str(first_sql).lower()
    assert "from roles src" in str(first_sql).lower()
    assert "on conflict (tenant_id, workspace, role_id) do nothing" in str(first_sql).lower()

    assert "insert into permissions" in str(second_sql).lower()
    assert "from permissions src" in str(second_sql).lower()
    assert (
        "on conflict (tenant_id, workspace, permission_id) do nothing"
        in str(second_sql).lower()
    )

    assert "insert into role_permissions" in str(third_sql).lower()
    assert "from role_permissions src" in str(third_sql).lower()
    assert (
        "on conflict (tenant_id, workspace, role_id, permission_id) do nothing"
        in str(third_sql).lower()
    )
