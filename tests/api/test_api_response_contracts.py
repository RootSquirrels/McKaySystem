"""Contract tests for normalized public API response envelopes."""

from __future__ import annotations

from typing import Any

from apps.flask_api import flask_app


def _response_json(response_value: Any) -> tuple[dict[str, Any], int]:
    """Extract JSON payload and status from a Flask response tuple.

    Args:
        response_value: Flask response tuple returned by helper functions.

    Returns:
        Tuple of decoded JSON payload and HTTP status code.
    """
    response, status = response_value
    return (response.get_json() or {}), int(status)


def test_lifecycle_internal_errors_use_standard_error_envelope() -> None:
    """Lifecycle 500 responses should use the standard public error envelope."""
    with flask_app.app.test_request_context("/api/lifecycle/ignore"):
        payload, status = _response_json(
            flask_app._api_internal_error_response(RuntimeError("boom"))
        )

    assert status == 500
    assert payload == {
        "ok": False,
        "error": "internal_error",
        "message": "internal error",
        "detail": "boom",
    }


def test_facets_internal_errors_use_standard_error_envelope() -> None:
    """Facet 500 responses should use the same normalized error contract."""
    with flask_app.app.test_request_context("/api/facets"):
        payload, status = _response_json(
            flask_app._api_internal_error_response(RuntimeError("facet boom"))
        )

    assert status == 500
    assert payload == {
        "ok": False,
        "error": "internal_error",
        "message": "internal error",
        "detail": "facet boom",
    }


def test_groups_internal_errors_use_standard_error_envelope() -> None:
    """Groups 500 responses should use the normalized public error contract."""
    with flask_app.app.test_request_context("/api/groups"):
        payload, status = _response_json(
            flask_app._api_internal_error_response(RuntimeError("group boom"))
        )

    assert status == 500
    assert payload == {
        "ok": False,
        "error": "internal_error",
        "message": "internal error",
        "detail": "group boom",
    }


def test_runs_diff_internal_errors_use_standard_error_envelope() -> None:
    """Runs diff 500 responses should use the normalized public error contract."""
    with flask_app.app.test_request_context("/api/runs/diff/latest"):
        payload, status = _response_json(
            flask_app._api_internal_error_response(RuntimeError("diff boom"))
        )

    assert status == 500
    assert payload == {
        "ok": False,
        "error": "internal_error",
        "message": "internal error",
        "detail": "diff boom",
    }
