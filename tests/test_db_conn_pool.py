"""Tests for pooled DB connection lifecycle behavior."""

from __future__ import annotations

from typing import Any

import apps.backend.db as db_mod


class _FakeConn:
    """Minimal fake psycopg2 connection."""

    def __init__(self, *, rollback_raises: bool = False) -> None:
        self.rollback_calls = 0
        self.close_calls = 0
        self.executed_statements: list[tuple[str, tuple[Any, ...]]] = []
        self._rollback_raises = rollback_raises

    def cursor(self) -> _FakeCursor:
        """Return a fake cursor context manager."""
        return _FakeCursor(self)

    def rollback(self) -> None:
        """Record rollback and optionally raise."""
        self.rollback_calls += 1
        if self._rollback_raises:
            raise RuntimeError("rollback failed")

    def close(self) -> None:
        """Record close call."""
        self.close_calls += 1


class _FakePool:
    """Minimal fake pool exposing getconn/putconn."""

    def __init__(self, conn: _FakeConn, *, put_raises: bool = False) -> None:
        self._conn = conn
        self.put_calls = 0
        self._put_raises = put_raises

    def getconn(self) -> _FakeConn:
        """Return the managed fake connection."""
        return self._conn

    def putconn(self, conn: _FakeConn) -> None:
        """Record putconn call and optionally raise."""
        assert conn is self._conn
        self.put_calls += 1
        if self._put_raises:
            raise RuntimeError("putconn failed")


class _FakeCursor:
    """Minimal fake cursor context manager."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def __enter__(self) -> _FakeCursor:
        """Return the cursor itself."""
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        """Do not suppress exceptions."""
        return False

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        """Record executed SQL."""
        self._conn.executed_statements.append((sql, params))


def test_db_conn_rolls_back_before_return(monkeypatch: Any) -> None:
    """db_conn should rollback before returning a connection to pool."""
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(db_mod, "_get_pool", lambda: pool)

    with db_mod.db_conn() as acquired:
        assert acquired is conn

    assert conn.rollback_calls == 1
    assert pool.put_calls == 1
    assert conn.close_calls == 0


def test_db_conn_still_returns_connection_when_rollback_fails(monkeypatch: Any) -> None:
    """Rollback failures should not prevent returning the connection to pool."""
    conn = _FakeConn(rollback_raises=True)
    pool = _FakePool(conn)
    monkeypatch.setattr(db_mod, "_get_pool", lambda: pool)

    with db_mod.db_conn():
        pass

    assert conn.rollback_calls == 1
    assert pool.put_calls == 1
    assert conn.close_calls == 0


def test_db_conn_closes_when_putconn_fails(monkeypatch: Any) -> None:
    """If putconn fails, db_conn should close the connection."""
    conn = _FakeConn()
    pool = _FakePool(conn, put_raises=True)
    monkeypatch.setattr(db_mod, "_get_pool", lambda: pool)

    with db_mod.db_conn():
        pass

    assert conn.rollback_calls == 1
    assert pool.put_calls == 1
    assert conn.close_calls == 1


def test_db_conn_applies_statement_timeout_on_checkout(monkeypatch: Any) -> None:
    """db_conn should set statement_timeout through a psycopg2 cursor."""
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(db_mod, "_get_pool", lambda: pool)

    class _DummySettings:
        class db:  # pylint: disable=too-few-public-methods,missing-class-docstring
            statement_timeout_ms = 2500

    monkeypatch.setattr(db_mod, "get_settings", lambda reload=True: _DummySettings())

    with db_mod.db_conn():
        pass

    assert conn.executed_statements == [("SET statement_timeout = %s", (2500,))]


def test_db_conn_skips_statement_timeout_when_unset(monkeypatch: Any) -> None:
    """db_conn should not issue a SET statement when timeout is unset."""
    conn = _FakeConn()
    pool = _FakePool(conn)
    monkeypatch.setattr(db_mod, "_get_pool", lambda: pool)

    class _DummySettings:
        class db:  # pylint: disable=too-few-public-methods,missing-class-docstring
            statement_timeout_ms = None

    monkeypatch.setattr(db_mod, "get_settings", lambda reload=True: _DummySettings())

    with db_mod.db_conn():
        pass

    assert conn.executed_statements == []
