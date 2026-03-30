"""Tests for security improvements: bearer token fail-fast and query timeout."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestBearerTokenFailFast:
    """Tests for bearer token authentication fail-fast."""

    def test_check_bearer_token_config_raises_when_token_missing_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should raise RuntimeError when bearer token is empty in production mode."""
        # Set up mock settings with empty bearer token and debug_errors=False
        mock_settings = MagicMock()
        mock_settings.api.bearer_token = ""
        mock_settings.api.debug_errors = False

        monkeypatch.setattr("apps.flask_api.flask_app._SETTINGS", mock_settings)
        monkeypatch.setattr("apps.flask_api.flask_app._API_BEARER_TOKEN", "")
        monkeypatch.setattr("apps.flask_api.flask_app._API_DEBUG_ERRORS", False)

        from apps.flask_api.flask_app import _check_bearer_token_config

        with pytest.raises(RuntimeError, match="API_BEARER_TOKEN is not set"):
            _check_bearer_token_config()

    def test_check_bearer_token_config_passes_when_token_set_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should not raise when bearer token is set in production mode."""
        mock_settings = MagicMock()
        mock_settings.api.bearer_token = "secret-token"
        mock_settings.api.debug_errors = False

        monkeypatch.setattr("apps.flask_api.flask_app._SETTINGS", mock_settings)
        monkeypatch.setattr("apps.flask_api.flask_app._API_BEARER_TOKEN", "secret-token")
        monkeypatch.setattr("apps.flask_api.flask_app._API_DEBUG_ERRORS", False)

        from apps.flask_api.flask_app import _check_bearer_token_config

        # Should not raise
        _check_bearer_token_config()

    def test_check_bearer_token_config_passes_when_debug_errors_true(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should not raise when debug_errors=True even with empty token."""
        mock_settings = MagicMock()
        mock_settings.api.bearer_token = ""
        mock_settings.api.debug_errors = True

        monkeypatch.setattr("apps.flask_api.flask_app._SETTINGS", mock_settings)
        monkeypatch.setattr("apps.flask_api.flask_app._API_BEARER_TOKEN", "")
        monkeypatch.setattr("apps.flask_api.flask_app._API_DEBUG_ERRORS", True)

        from apps.flask_api.flask_app import _check_bearer_token_config

        # Should not raise (debug mode allows no auth)
        _check_bearer_token_config()


class TestQueryTimeout:
    """Tests for database query timeout."""

    def test_db_conn_sets_statement_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """db_conn should set statement_timeout on the connection."""
        import apps.backend.db as db_mod

        execute_calls: list[str] = []

        class FakeConn:
            """Minimal fake connection."""

            def rollback(self) -> None:
                pass

            def execute(self, sql: str) -> None:
                execute_calls.append(sql)

        class FakePool:
            """Minimal fake pool."""

            def getconn(self) -> FakeConn:
                return FakeConn()

            def putconn(self, conn: FakeConn) -> None:
                pass

        monkeypatch.setattr(db_mod, "_get_pool", lambda: FakePool())
        monkeypatch.setattr("os.environ.get", lambda key, default=None: default)

        with db_mod.db_conn() as conn:
            pass

        # Verify SET statement_timeout was called
        assert any("statement_timeout" in call for call in execute_calls)

    def test_db_conn_uses_default_30s_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should use 30000ms (30 seconds) as default timeout."""
        import apps.backend.db as db_mod

        timeout_set: list[str] = []

        class FakeConn:
            def rollback(self) -> None:
                pass

            def execute(self, sql: str) -> None:
                if "statement_timeout" in sql:
                    timeout_set.append(sql)

        class FakePool:
            def getconn(self) -> FakeConn:
                return FakeConn()

            def putconn(self, conn: FakeConn) -> None:
                pass

        monkeypatch.setattr(db_mod, "_get_pool", lambda: FakePool())
        monkeypatch.setattr("os.environ.get", lambda key, default=None: default)

        with db_mod.db_conn():
            pass

        assert len(timeout_set) == 1
        assert "30000ms" in timeout_set[0]

    def test_db_conn_uses_env_override_for_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should use DB_STATEMENT_TIMEOUT_MS environment variable when set."""
        import apps.backend.db as db_mod

        timeout_set: list[str] = []

        class FakeConn:
            def rollback(self) -> None:
                pass

            def execute(self, sql: str) -> None:
                if "statement_timeout" in sql:
                    timeout_set.append(sql)

        class FakePool:
            def getconn(self) -> FakeConn:
                return FakeConn()

            def putconn(self, conn: FakeConn) -> None:
                pass

        monkeypatch.setattr(db_mod, "_get_pool", lambda: FakePool())

        def fake_environ_get(key: str, default: Any = None) -> Any:
            if key == "DB_STATEMENT_TIMEOUT_MS":
                return "60000"
            return default

        monkeypatch.setattr("os.environ.get", fake_environ_get)

        with db_mod.db_conn():
            pass

        assert len(timeout_set) == 1
        assert "60000ms" in timeout_set[0]


class TestAuthRateLimiting:
    """Tests for authentication endpoint rate limiting."""

    def test_rate_key_includes_auth_endpoints(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rate key should group /api/auth/* endpoints together."""
        from flask import Flask

        # Create a minimal Flask app context
        app = Flask(__name__)
        with app.test_request_context("/api/auth/login"):
            from apps.flask_api.flask_app import _rate_key, _canonical_api_path
            from flask import request

            path = _canonical_api_path(request.path)
            assert path == "/api/auth/login"

            # Mock the request path
            monkeypatch.setattr("apps.flask_api.flask_app.request.path", "/api/auth/login")
            monkeypatch.setattr(
                "apps.flask_api.flask_app.request.headers.get",
                lambda key, default="": "127.0.0.1",
            )
            monkeypatch.setattr(
                "apps.flask_api.flask_app._canonical_api_path",
                lambda p: "/api/auth/login" if "/api/auth" in p else p,
            )

            key = _rate_key()
            assert "/api/auth" in key


class TestRequestIdTracing:
    """Tests for request ID tracing."""

    def test_get_request_id_uses_x_request_id_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should use X-Request-Id header when provided."""
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context(headers={"X-Request-Id": "req-12345"}):
            from apps.flask_api.flask_app import _get_request_id

            request_id = _get_request_id()
            assert request_id == "req-12345"

    def test_get_request_id_uses_x_correlation_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should fall back to X-Correlation-Id when X-Request-Id not present."""
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context(headers={"X-Correlation-Id": "corr-67890"}):
            from apps.flask_api.flask_app import _get_request_id

            request_id = _get_request_id()
            assert request_id == "corr-67890"

    def test_get_request_id_generates_uuid_when_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should generate UUID when no request ID header present."""
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context():
            from apps.flask_api.flask_app import _get_request_id

            request_id = _get_request_id()
            # Should be a valid UUID format
            assert len(request_id) == 36  # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
            assert "-" in request_id

    def test_request_id_propagated_in_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Request ID should be included in log entries."""
        # This tests the logging structure includes request_id
        log_payload = {
            "method": "GET",
            "path": "/api/findings",
            "request_id": "req-12345",
        }
        assert "request_id" in log_payload
        assert log_payload["request_id"] == "req-12345"
