"""Coverage artifact models for worker scan visibility."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

COVERAGE_DIRNAME = "coverage"
COVERAGE_RESULTS_FILENAME = "coverage.jsonl"
COVERAGE_ISSUES_FILENAME = "issues.jsonl"


@dataclass(frozen=True)
class CoverageResult:
    """Structured outcome for one checker execution target."""

    tenant_id: str
    workspace: str
    run_id: str
    account_id: str
    region: str
    service: str
    checker_id: str
    checker_scope: str
    status: str
    findings_count: int = 0
    duration_ms: int | None = None
    confidence: str = "none"
    completeness_pct: float | None = None
    permission_gap_count: int = 0
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    skip_reason: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CoverageResult:
        """Hydrate one coverage result from a dict payload."""
        return cls(
            tenant_id=str(payload.get("tenant_id") or "").strip(),
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            account_id=str(payload.get("account_id") or "").strip(),
            region=str(payload.get("region") or "").strip(),
            service=str(payload.get("service") or "").strip(),
            checker_id=str(payload.get("checker_id") or "").strip(),
            checker_scope=str(payload.get("checker_scope") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            findings_count=int(payload.get("findings_count") or 0),
            duration_ms=(
                int(payload["duration_ms"])
                if payload.get("duration_ms") is not None
                else None
            ),
            confidence=str(payload.get("confidence") or "none").strip(),
            completeness_pct=(
                float(payload["completeness_pct"])
                if payload.get("completeness_pct") is not None
                else None
            ),
            permission_gap_count=int(payload.get("permission_gap_count") or 0),
            error_class=(str(payload.get("error_class") or "").strip() or None),
            error_code=(str(payload.get("error_code") or "").strip() or None),
            error_message=(str(payload.get("error_message") or "").strip() or None),
            skip_reason=(str(payload.get("skip_reason") or "").strip() or None),
            started_at=(str(payload.get("started_at") or "").strip() or None),
            finished_at=(str(payload.get("finished_at") or "").strip() or None),
        )


@dataclass(frozen=True)
class CoverageIssue:
    """Structured issue encountered while executing a checker target."""

    tenant_id: str
    workspace: str
    run_id: str
    account_id: str
    region: str
    service: str
    checker_id: str
    issue_type: str
    operation: str | None = None
    error_code: str | None = None
    message: str | None = None
    is_retryable: bool = False
    severity: str = "info"
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CoverageIssue:
        """Hydrate one coverage issue from a dict payload."""
        raw_payload = payload.get("payload")
        issue_payload = raw_payload if isinstance(raw_payload, dict) else None
        return cls(
            tenant_id=str(payload.get("tenant_id") or "").strip(),
            workspace=str(payload.get("workspace") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            account_id=str(payload.get("account_id") or "").strip(),
            region=str(payload.get("region") or "").strip(),
            service=str(payload.get("service") or "").strip(),
            checker_id=str(payload.get("checker_id") or "").strip(),
            issue_type=str(payload.get("issue_type") or "").strip(),
            operation=(str(payload.get("operation") or "").strip() or None),
            error_code=(str(payload.get("error_code") or "").strip() or None),
            message=(str(payload.get("message") or "").strip() or None),
            is_retryable=bool(payload.get("is_retryable")),
            severity=str(payload.get("severity") or "info").strip(),
            payload=issue_payload,
        )


def coverage_dir(base_dir: str | Path) -> Path:
    """Return the canonical coverage artifact directory for one run."""
    return Path(base_dir) / COVERAGE_DIRNAME


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write JSONL rows deterministically to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    return path


def write_coverage_bundle(
    base_dir: str | Path,
    *,
    results: list[CoverageResult],
    issues: list[CoverageIssue],
) -> Path:
    """Write coverage results and issues under the run artifact directory."""
    out_dir = coverage_dir(base_dir)
    _write_jsonl(out_dir / COVERAGE_RESULTS_FILENAME, [item.to_dict() for item in results])
    _write_jsonl(out_dir / COVERAGE_ISSUES_FILENAME, [item.to_dict() for item in issues])
    return out_dir


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL rows from *path* when present."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def load_coverage_bundle(base_dir: str | Path) -> tuple[list[CoverageResult], list[CoverageIssue]]:
    """Load coverage results and issues from one coverage artifact directory."""
    in_dir = Path(base_dir)
    results = [
        CoverageResult.from_dict(item)
        for item in _load_jsonl(in_dir / COVERAGE_RESULTS_FILENAME)
    ]
    issues = [
        CoverageIssue.from_dict(item)
        for item in _load_jsonl(in_dir / COVERAGE_ISSUES_FILENAME)
    ]
    return results, issues
