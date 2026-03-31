"""
apps.worker.runner

FinOps SaaS runner (checkers -> validated wire findings -> storage-cast -> parquet).

Default behavior: run ALL registered checkers (discovered under the `checks` package).
Optional behavior: run only selected checkers via --checker, and/or exclude via --exclude-checker.

NEW: Correlation step
--------------------
After writing raw findings to Parquet, the runner can optionally run the correlation engine,
which reads the raw Parquet dataset and emits *meta-findings* to a separate Parquet dataset.

Pipeline:
  checkers -> findings_raw parquet
    -> correlation -> findings_correlated parquet
      -> duckdb/json export (should UNION both datasets)

Multi-region execution
----------------------
Regions are configured in infra/aws_config.py (AWS_REGIONS).
No CLI args are used for region selection.

Run everything (default):
python -m apps.worker.runner --tenant acme --workspace prod

Disable correlation:
python -m apps.worker.runner --tenant acme --workspace prod --no-correlation

Custom correlated output directory:
python -m apps.worker.runner --tenant acme --workspace prod --correlation-out data/finops_findings_correlated

Run everything except one checker:
python -m apps.worker.runner --tenant acme --workspace prod \
  --exclude-checker checks.aws.s3_lifecycle_missing:S3LifecycleMissingChecker

Run a subset:
python -m apps.worker.runner --tenant acme --workspace prod --checker checks.aws.s3_lifecycle_missing:S3LifecycleMissingChecker
"""

from __future__ import annotations

import argparse
import glob
import importlib
import logging
import os
import pkgutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import boto3

import checks  # IMPORTANT: used for module discovery
from apps.worker.coverage_model import CoverageIssue, CoverageResult, write_coverage_bundle
from apps.worker.resource_graph_model import build_graph_from_findings, write_graph_bundle
from checks.registry import get_factory, list_specs
from contracts.finops_checker_pattern import Checker, CheckerRunner, RunContext
from contracts.services import Services, ServicesFactory
from infra.aws_config import SDK_CONFIG
from infra.config import get_settings
from infra.logging_config import setup_logging
from infra.pipeline_paths import PipelinePaths
from pipeline.run_manifest import RunManifest, write_manifest
from pipeline.writer_parquet import FindingsParquetWriter, ParquetWriterConfig
from version import ENGINE_NAME, ENGINE_VERSION, RULEPACK_VERSION, SCHEMA_VERSION

logger = logging.getLogger(__name__)


def _iso_z(dt: datetime) -> str:
    """Return UTC ISO-8601 with trailing Z."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clean_path(value: str) -> str:
    return str(value or "").strip()


def _derive_sibling_dir(raw_out_dir: str, sibling_name: str) -> str:
    if not raw_out_dir:
        return ""
    try:
        return str(Path(raw_out_dir).parent / sibling_name)
    except Exception:
        return ""


def _has_parquet(globs_list: Sequence[str]) -> bool:
    for g in globs_list:
        if glob.glob(g, recursive=True):
            return True
    return False


def _non_empty_dir(path: str) -> bool:
    return os.path.isdir(path) and bool(glob.glob(f"{path}/**/*.parquet", recursive=True))


def _optional_non_empty_text(value: Any) -> str | None:
    """Return normalized optional text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _checker_service(checker: Checker, spec: str) -> str:
    """Best-effort service name for coverage rows."""
    explicit = str(getattr(checker, "service", "") or "").strip()
    if explicit:
        return explicit
    checker_id = str(getattr(checker, "checker_id", "") or "").strip()
    if checker_id.startswith("aws."):
        parts = checker_id.split(".")
        if len(parts) >= 2:
            return parts[1]
    if spec.startswith("checks.aws."):
        parts = spec.split(".")
        if len(parts) >= 3:
            return parts[2].split(":")[0]
    return "unknown"


def _coverage_confidence(*, invalid_findings: int) -> str:
    """Derive a simple deterministic confidence label."""
    if invalid_findings > 0:
        return "medium"
    return "high"


def _is_permission_gap_finding(record: dict[str, Any]) -> bool:
    """Return True when a validated finding represents a missing-permission signal."""
    check_id = str(record.get("check_id") or "").strip().lower()
    if check_id.endswith(".missing.permission") or check_id.endswith(".access.error"):
        return True

    issue_key = record.get("issue_key")
    if isinstance(issue_key, dict):
        issue_values = {str(value).strip().lower() for value in issue_key.values() if value is not None}
        if "access_error" in issue_values:
            return True

    text = " ".join(
        [
            str(record.get("title") or "").strip().lower(),
            str(record.get("message") or "").strip().lower(),
            check_id,
        ]
    )
    return (
        "access denied" in text
        or "missing permission" in text
        or "unauthorizedoperation" in text
        or "unable to list" in text and ".access.error" in check_id
    )


def _permission_gap_findings_count(records: Sequence[dict[str, Any]]) -> int:
    """Count validated findings that indicate missing coverage due to permissions."""
    return sum(1 for record in records if _is_permission_gap_finding(record))


def _coverage_error_class(exc: Exception) -> tuple[str, bool, str | None]:
    """Map runtime exceptions to coverage issue classes."""
    code = None
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        err = resp.get("Error")
        if isinstance(err, dict):
            raw_code = err.get("Code")
            code = str(raw_code).strip() if raw_code is not None else None
    if code is None:
        raw_code = getattr(exc, "code", None)
        code = str(raw_code).strip() if raw_code is not None else None

    text = " ".join(
        [
            str(code or "").lower(),
            exc.__class__.__name__.lower(),
            str(exc).lower(),
        ]
    )
    if "accessdenied" in text or "access denied" in text or "unauthorized" in text:
        return "missing_permission", True, code
    if "throttl" in text or "toomanyrequests" in text or "rate exceeded" in text:
        return "throttled", False, code
    if isinstance(exc, (TypeError, ValueError)):
        return "malformed_source_data", False, code
    return "internal_checker_error", False, code


def _derive_pricing_metadata_from_services(services: Services) -> tuple[str | None, str | None]:
    """Best-effort derive pricing source/version from runtime services."""
    pricing = getattr(services, "pricing", None)
    if pricing is None:
        return None, None

    run_metadata = getattr(pricing, "run_metadata", None)
    if callable(run_metadata):
        try:
            metadata = run_metadata()
        except (AttributeError, KeyError, TypeError, ValueError):
            metadata = None
        if isinstance(metadata, dict):
            source = _optional_non_empty_text(
                metadata.get("pricing_source") or metadata.get("price_source")
            )
            version = _optional_non_empty_text(metadata.get("pricing_version"))
            return source, version

    source = _optional_non_empty_text(
        getattr(pricing, "pricing_source", None) or getattr(pricing, "source", None)
    )
    version = _optional_non_empty_text(
        getattr(pricing, "pricing_version", None) or getattr(pricing, "version", None)
    )
    return source, version


def _resolve_run_pricing_metadata(*, services: Services) -> tuple[str | None, str | None]:
    """Resolve run pricing metadata with explicit env override precedence."""
    auto_source, auto_version = _derive_pricing_metadata_from_services(services)

    worker_cfg = get_settings(reload=True).worker
    pricing_version = str(worker_cfg.pricing_version or "").strip()
    pricing_source = str(worker_cfg.pricing_source or "").strip()

    resolved_version = pricing_version or auto_version or ""
    resolved_source = pricing_source or auto_source or ""
    return (resolved_version or None), (resolved_source or None)


def _make_run_id(run_ts: datetime) -> str:
    return f"run-{run_ts.astimezone(UTC).isoformat().replace('+00:00', 'Z')}"


def _discover_all_checker_specs() -> list[str]:
    """
    Import all modules under the `checks` package so they can register factories/classes.
    Returns all registered checker specs in deterministic order.
    """
    prefix = checks.__name__ + "."
    for mod in pkgutil.walk_packages(checks.__path__, prefix):
        importlib.import_module(mod.name)

    specs = list_specs()
    if not specs:
        raise RuntimeError(
            "No checkers registered. Ensure checker modules register themselves in checks.registry."
        )
    return specs


def _load_checker(dotted_path: str, *, ctx: RunContext, bootstrap: dict) -> Checker:
    """
    Load a checker instance from a dotted import path.

    Format:
      module.path:ClassName
    Example:
      checks.aws.s3_lifecycle_missing:S3LifecycleMissingChecker
    """
    if ":" not in dotted_path:
        raise ValueError("Checker path must be like 'module.path:ClassName'")
    module_path, class_name = dotted_path.split(":", 1)

    # Importing the module can register a factory in checks.registry.
    module = importlib.import_module(module_path)

    # If a factory is registered for this spec, use it.
    factory = get_factory(dotted_path)
    if factory is not None:
        instance = factory(ctx, bootstrap)
        if not hasattr(instance, "run") or not hasattr(instance, "checker_id"):
            raise TypeError(f"Factory for '{dotted_path}' did not return a valid Checker")
        return instance

    # Fallback: plain no-arg constructor (legacy/simple checkers).
    klass = getattr(module, class_name, None)
    if klass is None:
        raise ValueError(f"Class '{class_name}' not found in module '{module_path}'")

    instance = klass()
    if not hasattr(instance, "run") or not hasattr(instance, "checker_id"):
        raise TypeError(f"{dotted_path} is not a valid Checker (missing run/checker_id)")
    return instance


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinOps runner (checkers -> findings parquet)")

    # User inputs (allowed)
    parser.add_argument("--tenant", required=True, help="Tenant identifier (e.g. acme)")
    parser.add_argument("--workspace", default="default", help="Workspace/environment (e.g. prod/dev)")
    parser.add_argument("--cloud", default="aws", choices=["aws", "azure", "gcp"], help="Cloud provider")
    parser.add_argument("--currency", default="USD", help="Default currency for actual.model.currency")

    parser.add_argument(
        "--out",
        default="",
        help="Output base directory for finops_findings parquet dataset "
        "(default: infra.pipeline_paths.PipelinePaths.findings_raw_dir())",
    )

    parser.add_argument(
        "--finding-id-mode",
        default="stable",
        choices=["stable", "per_run", "per_day"],
        help="How finding_id is salted (stable/per_run/per_day)",
    )

    # By default: run everything (registered).
    # If user provides --checker, run only those (after applying exclusions).
    parser.add_argument(
        "--checker",
        action="append",
        default=None,  # None means "user did not specify"
        help="Checker to run, format: module.path:ClassName. Repeatable. If omitted, runs all checkers.",
    )

    parser.add_argument(
        "--exclude-checker",
        action="append",
        default=[],
        help="Checker spec(s) to exclude (same format as --checker). Repeatable.",
    )

    parser.add_argument(
        "--drop-invalid-on-cast",
        action="store_true",
        help="If set, records failing storage casting are skipped instead of failing the run.",
    )

    # Correlation controls
    parser.add_argument(
        "--no-correlation",
        action="store_true",
        help="Disable correlation step (meta-findings).",
    )
    parser.add_argument(
        "--correlation-out",
        default="",
        help="Output base directory for correlated findings parquet dataset "
        "(default: infra.pipeline_paths.PipelinePaths.findings_correlated_dir())",
    )
    parser.add_argument(
        "--correlation-threads",
        type=int,
        default=4,
        help="DuckDB threads for correlation engine (default: 4).",
    )

    # Convenience
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="Print engine/rulepack/schema versions and exit.",
    )

    return parser.parse_args(argv)


def _run_correlation_step(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    findings_glob: str,
    out_dir: str,
    threads: int,
    finding_id_mode: str,
) -> dict:
    """
    Run correlation step if available.

    Returns a dict of stats for printing.
    """

    try:
        from pipeline.correlation.correlate_findings import run_correlation
    except Exception as exc:
        logger.warning("Correlation step skipped: not available (%s)", exc)
        return {"enabled": False, "emitted": 0, "errors": 0, "out_dir": out_dir}

    try:
        stats = run_correlation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            findings_glob=findings_glob,
            out_dir=out_dir,
            threads=int(threads),
            finding_id_mode=finding_id_mode,
        )
    except Exception:
        logger.exception("Correlation failed at runtime")
        return {"enabled": True, "emitted": 0, "errors": 1, "out_dir": out_dir, "failed": True}

    if not isinstance(stats, dict):
        return {"enabled": True, "emitted": 0, "errors": 0, "out_dir": out_dir}
    return stats


def run_cost_enrichment_if_available(
    *,
    tenant_id: str,
    findings_globs: list[str],
    raw_cur_globs: list[str],
    cur_facts_globs: list[str],
    enriched_out_dir: str,
) -> bool:
    """
    Normalize CUR (if raw files exist) and enrich findings with actual costs.

    Returns:
      True  -> enriched dataset was produced / updated
      False -> enrichment skipped (CUR unavailable)
    """
    try:
        from pipeline.cur.cost_enrich import CostEnrichConfig, enrich_findings_with_cur
        from pipeline.cur.normalize_cur import CurNormalizeConfig, normalize_cur
    except Exception as exc:  # pragma: no cover
        logger.warning("CUR enrichment unavailable (modules missing): %s", exc)
        return False

    if _has_parquet(raw_cur_globs):
        logger.info("CUR raw files detected, normalizing...")
        normalize_cur(
            CurNormalizeConfig(
                tenant_id=tenant_id,
                input_globs=list(raw_cur_globs),
                out_dir=str(PipelinePaths().cur_facts_dir()),
            )
        )
    else:
        logger.info("No raw CUR files detected, skipping normalization")

    if not _has_parquet(cur_facts_globs):
        logger.info("No CUR facts available, skipping cost enrichment")
        return False

    logger.info("Enriching findings with actual costs from CUR")
    enrich_findings_with_cur(
        CostEnrichConfig(
            tenant_id=tenant_id,
            findings_globs=findings_globs,
            cur_facts_globs=list(cur_facts_globs),
            out_dir=enriched_out_dir,
        )
    )
    return True


def _get_configured_regions() -> list[str]:
    """
    Read region list from configuration (infra/aws_config.py).

    Expected: AWS_REGIONS = ["eu-west-3", "us-east-1", ...]
    """
    try:
        from infra.aws_config import AWS_REGIONS  # type: ignore  # pylint: disable=import-error
    except Exception as exc:
        raise RuntimeError(
            "Multi-region runner requires AWS_REGIONS in infra/aws_config.py "
            "(e.g. AWS_REGIONS = ['eu-west-3'])."
        ) from exc

    regions = [str(r).strip() for r in (AWS_REGIONS or []) if str(r).strip()]
    if not regions:
        raise RuntimeError(
            "AWS_REGIONS is empty. Configure infra/aws_config.py with at least one region."
        )

    seen = set()
    ordered: list[str] = []
    for r in regions:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return ordered


def _make_ctx(
    *,
    args: argparse.Namespace,
    run_id: str,
    run_ts: datetime,
    services: Services,
) -> RunContext:
    return RunContext(
        tenant_id=args.tenant,
        workspace_id=args.workspace,
        run_id=run_id,
        run_ts=run_ts,
        engine_name=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        rulepack_version=RULEPACK_VERSION,
        schema_version=SCHEMA_VERSION,
        default_currency=args.currency,
        cloud=args.cloud,
        services=services,
    )


def _partition_checkers_by_scope(
    *,
    checker_specs: list[str],
    ctx_control: RunContext,
    bootstrap: dict,
) -> tuple[list[Checker], list[str], dict[str, dict[str, str]]]:
    """
    Instantiate once using the control ctx so we can detect checker.is_regional.

    Returns:
      - global_checkers: instantiated (run once)
      - regional_specs: specs to instantiate per region
    """
    global_checkers: list[Checker] = []
    regional_specs: list[str] = []
    coverage_meta: dict[str, dict[str, str]] = {}

    for spec in checker_specs:
        inst = _load_checker(spec, ctx=ctx_control, bootstrap=bootstrap)
        is_regional = bool(getattr(inst, "is_regional", True))
        coverage_meta[spec] = {
            "checker_id": str(getattr(inst, "checker_id", spec)).strip() or spec,
            "service": _checker_service(inst, spec),
            "checker_scope": "regional" if is_regional else "global",
        }
        if is_regional:
            regional_specs.append(spec)
        else:
            global_checkers.append(inst)

    return global_checkers, regional_specs, coverage_meta


def main(argv: Sequence[str]) -> int:
    args = _parse_args(argv)

    if args.print_version:
        print(f"ENGINE_NAME={ENGINE_NAME}")
        print(f"ENGINE_VERSION={ENGINE_VERSION}")
        print(f"RULEPACK_VERSION={RULEPACK_VERSION}")
        print(f"SCHEMA_VERSION={SCHEMA_VERSION}")
        return 0

    setup_logging(extra_fields={"app": "mckay", "component": "runner"})

    raw_arg = _clean_path(args.out)
    corr_arg = _clean_path(args.correlation_out)

    # If raw output is overridden, derive correlated/enriched defaults alongside it
    derived_corr = _derive_sibling_dir(raw_arg, "finops_findings_correlated") if raw_arg else ""
    derived_enriched = _derive_sibling_dir(raw_arg, "finops_findings_enriched") if raw_arg else ""

    paths = PipelinePaths.with_overrides(
        findings_raw_dir=raw_arg or None,
        findings_correlated_dir=corr_arg or (derived_corr or None),
        findings_enriched_dir=derived_enriched or None,
    )

    # Centralized defaults (CLI overrides still win)
    raw_out_dir = str(paths.findings_raw_dir())
    corr_out_dir = str(paths.findings_correlated_dir())
    enriched_out_dir = str(paths.findings_enriched_dir())

    run_ts = _utc_now()
    run_id = _make_run_id(run_ts)

    # --- Regions (config-driven) ---
    regions = _get_configured_regions()
    control_region = regions[0]

    # --- Services / AWS bootstrapping ---
    session = boto3.Session()
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]

    factory = ServicesFactory(session=session, sdk_config=SDK_CONFIG)

    # Bootstrap is runtime data that checker factories may need.
    bootstrap: dict = {
        "aws_account_id": account_id,
        "aws_billing_account_id": account_id,
    }

    # Control ctx (first region) is used for:
    #  - instantiating once to detect is_regional
    #  - running global checkers
    control_services = factory.for_region(control_region)
    ctx_control = _make_ctx(args=args, run_id=run_id, run_ts=run_ts, services=control_services)

    # --- Resolve which checkers to run ---
    if args.checker is None:
        checker_specs = _discover_all_checker_specs()
    else:
        checker_specs = list(args.checker)

    exclude = set(args.exclude_checker or [])
    checker_specs = [s for s in checker_specs if s not in exclude]

    if not checker_specs:
        raise RuntimeError("No checkers selected to run (after exclusions).")

    global_checkers, regional_specs, coverage_meta = _partition_checkers_by_scope(
        checker_specs=checker_specs,
        ctx_control=ctx_control,
        bootstrap=bootstrap,
    )

    runner = CheckerRunner(finding_id_salt_mode=args.finding_id_mode)

    writer = FindingsParquetWriter(
        ParquetWriterConfig(
            base_dir=raw_out_dir,
            drop_invalid_on_cast=bool(args.drop_invalid_on_cast),
        )
    )

    total_valid = 0
    total_invalid_count = 0
    total_invalid_errors: list[str] = []
    per_region_valid: dict[str, int] = {}
    checker_failures: list[str] = []
    coverage_results: dict[tuple[str, str, str, str], CoverageResult] = {}
    coverage_issues: list[CoverageIssue] = []
    graph_wire_records: list[dict[str, Any]] = []

    for spec in checker_specs:
        meta = coverage_meta[spec]
        if meta["checker_scope"] == "global":
            coverage_results[(account_id, "", meta["service"], meta["checker_id"])] = CoverageResult(
                tenant_id=args.tenant,
                workspace=args.workspace,
                run_id=run_id,
                account_id=account_id,
                region="",
                service=meta["service"],
                checker_id=meta["checker_id"],
                checker_scope=meta["checker_scope"],
                status="not_assessed",
            )
            continue

        for region in regions:
            coverage_results[(account_id, region, meta["service"], meta["checker_id"])] = CoverageResult(
                tenant_id=args.tenant,
                workspace=args.workspace,
                run_id=run_id,
                account_id=account_id,
                region=region,
                service=meta["service"],
                checker_id=meta["checker_id"],
                checker_scope=meta["checker_scope"],
                status="not_assessed",
            )

    # --- Run global checkers (once, in control region) ---
    for checker in global_checkers:
        checker_started = _utc_now()
        perf_started = perf_counter()
        key = (
            account_id,
            "",
            _checker_service(checker, str(getattr(checker, "checker_id", ""))),
            str(getattr(checker, "checker_id", "")).strip(),
        )
        try:
            result = runner.run_one(checker, ctx_control)
            writer.extend(result.valid_findings)
            graph_wire_records.extend(dict(item) for item in result.valid_findings)
            valid_count = len(result.valid_findings)
            permission_gap_count = _permission_gap_findings_count(result.valid_findings)
            total_valid += valid_count
            total_invalid_count += int(result.invalid_findings)
            total_invalid_errors.extend(result.invalid_errors or [])
            coverage_results[key] = CoverageResult(
                tenant_id=args.tenant,
                workspace=args.workspace,
                run_id=run_id,
                account_id=account_id,
                region="",
                service=key[2],
                checker_id=key[3],
                checker_scope="global",
                status="assessed_with_findings" if valid_count > 0 else "assessed_no_issue",
                findings_count=valid_count,
                duration_ms=int((perf_counter() - perf_started) * 1000),
                confidence=_coverage_confidence(invalid_findings=result.invalid_findings),
                completeness_pct=100.0,
                permission_gap_count=permission_gap_count,
                started_at=_iso_z(checker_started),
                finished_at=_iso_z(_utc_now()),
            )
        except Exception as exc:  # strictly required to classify scan failures without hiding scope gaps
            logger.exception("Checker failed: %s", key[3])
            error_class, is_permission_gap, error_code = _coverage_error_class(exc)
            checker_failures.append(key[3])
            coverage_results[key] = CoverageResult(
                tenant_id=args.tenant,
                workspace=args.workspace,
                run_id=run_id,
                account_id=account_id,
                region="",
                service=key[2],
                checker_id=key[3],
                checker_scope="global",
                status="assessment_failed",
                findings_count=0,
                duration_ms=int((perf_counter() - perf_started) * 1000),
                confidence="none",
                completeness_pct=0.0,
                permission_gap_count=1 if is_permission_gap else 0,
                error_class=error_class,
                error_code=error_code,
                error_message=str(exc)[:1000],
                started_at=_iso_z(checker_started),
                finished_at=_iso_z(_utc_now()),
            )
            coverage_issues.append(
                CoverageIssue(
                    tenant_id=args.tenant,
                    workspace=args.workspace,
                    run_id=run_id,
                    account_id=account_id,
                    region="",
                    service=key[2],
                    checker_id=key[3],
                    issue_type=error_class,
                    error_code=error_code,
                    message=str(exc)[:1000],
                    is_retryable=(error_class == "throttled"),
                    severity="warning" if is_permission_gap else "error",
                    payload={"exception_type": exc.__class__.__name__},
                )
            )

    # --- Run regional checkers per configured region ---
    for region in regions:
        svcs = factory.for_region(region)
        ctx_region = _make_ctx(args=args, run_id=run_id, run_ts=run_ts, services=svcs)

        regional_checkers: list[Checker] = []
        for spec in regional_specs:
            regional_checkers.append(_load_checker(spec, ctx=ctx_region, bootstrap=bootstrap))

        region_valid_total = 0
        if not regional_checkers:
            per_region_valid[region] = 0
            continue

        for checker in regional_checkers:
            checker_started = _utc_now()
            perf_started = perf_counter()
            key = (
                account_id,
                region,
                _checker_service(checker, str(getattr(checker, "checker_id", ""))),
                str(getattr(checker, "checker_id", "")).strip(),
            )
            try:
                result = runner.run_one(checker, ctx_region)
                writer.extend(result.valid_findings)
                graph_wire_records.extend(dict(item) for item in result.valid_findings)
                valid_count = len(result.valid_findings)
                permission_gap_count = _permission_gap_findings_count(result.valid_findings)
                region_valid_total += valid_count
                total_valid += valid_count
                total_invalid_count += int(result.invalid_findings)
                total_invalid_errors.extend(result.invalid_errors or [])
                coverage_results[key] = CoverageResult(
                    tenant_id=args.tenant,
                    workspace=args.workspace,
                    run_id=run_id,
                    account_id=account_id,
                    region=region,
                    service=key[2],
                    checker_id=key[3],
                    checker_scope="regional",
                    status="assessed_with_findings" if valid_count > 0 else "assessed_no_issue",
                    findings_count=valid_count,
                    duration_ms=int((perf_counter() - perf_started) * 1000),
                    confidence=_coverage_confidence(invalid_findings=result.invalid_findings),
                    completeness_pct=100.0,
                    permission_gap_count=permission_gap_count,
                    started_at=_iso_z(checker_started),
                    finished_at=_iso_z(_utc_now()),
                )
            except Exception as exc:  # strictly required to classify scan failures without aborting visibility
                logger.exception("Checker failed: %s region=%s", key[3], region)
                error_class, is_permission_gap, error_code = _coverage_error_class(exc)
                checker_failures.append(f"{key[3]}@{region}")
                coverage_results[key] = CoverageResult(
                    tenant_id=args.tenant,
                    workspace=args.workspace,
                    run_id=run_id,
                    account_id=account_id,
                    region=region,
                    service=key[2],
                    checker_id=key[3],
                    checker_scope="regional",
                    status="assessment_failed",
                    findings_count=0,
                    duration_ms=int((perf_counter() - perf_started) * 1000),
                    confidence="none",
                    completeness_pct=0.0,
                    permission_gap_count=1 if is_permission_gap else 0,
                    error_class=error_class,
                    error_code=error_code,
                    error_message=str(exc)[:1000],
                    started_at=_iso_z(checker_started),
                    finished_at=_iso_z(_utc_now()),
                )
                coverage_issues.append(
                    CoverageIssue(
                        tenant_id=args.tenant,
                        workspace=args.workspace,
                        run_id=run_id,
                        account_id=account_id,
                        region=region,
                        service=key[2],
                        checker_id=key[3],
                        issue_type=error_class,
                        error_code=error_code,
                        message=str(exc)[:1000],
                        is_retryable=(error_class == "throttled"),
                        severity="warning" if is_permission_gap else "error",
                        payload={"exception_type": exc.__class__.__name__},
                    )
                )

        per_region_valid[region] = region_valid_total

    stats = writer.close()

    # --- Optional: Correlation step (meta-findings) ---
    corr_stats: dict = {"enabled": False, "emitted": 0, "errors": 0, "out_dir": ""}

    if not args.no_correlation:
        raw_glob = paths.raw_findings_glob()
        corr_stats = _run_correlation_step(
            tenant_id=args.tenant,
            workspace_id=args.workspace,
            run_id=run_id,
            findings_glob=raw_glob,
            out_dir=corr_out_dir,
            threads=args.correlation_threads,
            finding_id_mode=args.finding_id_mode,
        )

    # --- Optional: CUR cost enrichment (best-effort) ---
    run_cost_enrichment_if_available(
        tenant_id=args.tenant,
        findings_globs=[
            paths.raw_findings_glob(),
            paths.correlated_findings_glob(),
        ],
        raw_cur_globs=[
            str(paths.cur_raw_dir() / "**/*.parquet"),
        ],
        cur_facts_globs=[
            str(paths.cur_facts_dir() / "**/*.parquet"),
        ],
        enriched_out_dir=enriched_out_dir,
    )

    # --- Environment ---
    logger.info("python: %s", sys.version.replace("\n", " "))
    logger.info("cwd: %s", os.getcwd())

    # --- Summary ---
    logger.info("=== Run summary ===")
    logger.info("tenant: %s", args.tenant)
    logger.info("workspace: %s", args.workspace)
    logger.info("cloud: %s", args.cloud)
    logger.info("run_id: %s", run_id)
    logger.info("run_ts: %s", run_ts.astimezone(UTC).isoformat().replace("+00:00", "Z"))

    logger.info("regions_configured: %s", len(regions))
    logger.info("regions: %s", ", ".join(regions))
    logger.info("control_region: %s", control_region)

    logger.info("out_raw: %s", raw_out_dir)
    logger.info("out_correlated: %s", corr_out_dir)
    logger.info("out_enriched: %s", enriched_out_dir)

    logger.info("checkers_selected: %s", len(checker_specs))
    logger.info("global_checkers: %s", len(global_checkers))
    logger.info("regional_checkers: %s", len(regional_specs))

    if per_region_valid:
        logger.info("--- Findings per region ---")
        for r in regions:
            logger.info("%s: %s", r, per_region_valid.get(r, 0))

    logger.info("engine_name: %s", ENGINE_NAME)
    logger.info("engine_version: %s", ENGINE_VERSION)
    logger.info("rulepack_version: %s", RULEPACK_VERSION)
    logger.info("schema_version: %s", SCHEMA_VERSION)

    logger.info("valid_findings: %s", total_valid)
    logger.info("invalid_findings: %s", total_invalid_count)
    logger.info("coverage_targets: %s", len(coverage_results))
    logger.info("coverage_failures: %s", len(checker_failures))
    logger.info("coverage_issues: %s", len(coverage_issues))

    logger.info("writer_received: %s", stats.received)
    logger.info("writer_written: %s", stats.written)
    logger.info("writer_dropped_cast_errors: %s", stats.dropped_cast_errors)

    # Correlation summary
    if corr_stats.get("enabled"):
        logger.info("--- Correlation ---")
        logger.info("correlation_out: %s", corr_stats.get("out_dir", ""))
        logger.info("correlation_rules_enabled: %s", corr_stats.get("rules_enabled", ""))
        logger.info("correlation_emitted: %s", corr_stats.get("emitted", 0))
        logger.info("correlation_errors: %s", corr_stats.get("errors", 0))
    else:
        logger.info("--- Correlation ---")
        logger.info("correlation: disabled/skipped")

    if total_invalid_errors:
        logger.info("--- Sample validation errors (contract layer) ---")
        for e in total_invalid_errors[:10]:
            logger.info("- %s", e)

    if stats.cast_errors:
        logger.info("--- Sample storage cast errors (storage boundary) ---")
        for e in stats.cast_errors[:10]:
            logger.info("- %s", e)

    # --- Error counts ---
    logger.info("validation_errors_count: %s", len(total_invalid_errors))
    logger.info("cast_errors_count: %s", len(stats.cast_errors or []))

    # --- Persist run manifest (single source of truth across steps) ---
    # Downstream steps (export/ingest) should NOT rely on hidden defaults for
    # tenant/workspace or dataset paths.
    try:
        pricing_version, pricing_source = _resolve_run_pricing_metadata(services=control_services)
        coverage_out_dir = write_coverage_bundle(
            raw_out_dir,
            results=sorted(
                coverage_results.values(),
                key=lambda item: (
                    item.account_id,
                    item.region,
                    item.service,
                    item.checker_id,
                ),
            ),
            issues=sorted(
                coverage_issues,
                key=lambda item: (
                    item.account_id,
                    item.region,
                    item.service,
                    item.checker_id,
                    item.issue_type,
                    item.error_code or "",
                ),
            ),
        )
        graph_nodes, graph_edges = build_graph_from_findings(
            graph_wire_records,
            tenant_id=args.tenant,
            workspace=args.workspace,
            run_id=run_id,
        )
        graph_out_dir = write_graph_bundle(
            raw_out_dir,
            nodes=graph_nodes,
            edges=graph_edges,
        )
        manifest = RunManifest(
            tenant_id=args.tenant,
            workspace=args.workspace,
            run_id=run_id,
            run_ts=_iso_z(run_ts),
            engine_name=ENGINE_NAME,
            engine_version=ENGINE_VERSION,
            rulepack_version=RULEPACK_VERSION,
            schema_version=SCHEMA_VERSION,
            pricing_version=pricing_version,
            pricing_source=pricing_source,
            out_raw=str(raw_out_dir),
            out_correlated=str(corr_out_dir),
            out_enriched=str(enriched_out_dir),
            coverage_dir=str(coverage_out_dir),
            graph_dir=str(graph_out_dir),
            export_dir=str(paths.export_dir()),
        )
        mp = write_manifest(raw_out_dir, manifest)
        logger.info("run_manifest: %s", mp)
    except Exception as exc:  # pragma: no cover
        # Never fail the run for a manifest write, but make it loud.
        logger.warning("failed to write run manifest: %s", exc)

    # Non-zero exit code if nothing was written but we did receive records
    if stats.written == 0 and stats.received > 0:
        return 2

    if corr_stats.get("enabled") and int(corr_stats.get("errors", 0)) > 0:
        return 3
    if corr_stats.get("failed"):
        return 3
    if checker_failures:
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
