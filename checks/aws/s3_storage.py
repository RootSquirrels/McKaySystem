"""S3 storage and governance checker.

This checker emits deterministic bucket-level governance and storage
optimization findings using best-effort read-only AWS APIs.

Emitted check_ids:
  - aws.s3.governance.lifecycle.missing
  - aws.s3.governance.encryption.missing
  - aws.s3.governance.public.access.block.missing
  - aws.s3.cost.bucket.storage.estimate
  - aws.s3.cost.lifecycle.transition.review
  - aws.s3.cost.multipart.upload.cleanup
  - aws.s3.cost.replication.review
  - aws.s3.cost.intelligent_tiering.review
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from botocore.client import BaseClient
from botocore.exceptions import ClientError

from checks.aws._common import (
    AwsAccountContext,
    PricingResolver,
    build_scope,
    get_logger,
    now_utc,
)
from checks.aws.defaults import S3_DEFAULT_STORAGE_PRICE_GB_MONTH_USD, S3_METRIC_LOOKBACK_DAYS
from checks.registry import Bootstrap, register_checker
from contracts.finops_checker_pattern import FindingDraft, RunContext, Scope, Severity

# Logger for this module
_LOGGER = get_logger("s3_storage")


def _normalize_s3_location_constraint(value: str | None) -> str:
    """Normalize S3 GetBucketLocation LocationConstraint values."""
    if not value:
        return "us-east-1"
    if value == "EU":
        return "eu-west-1"
    return str(value)


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "") or "")


def _bytes_to_gib(value: float) -> float:
    return float(value) / (1024.0 ** 3)


def _days_between(now: datetime, then: datetime) -> int:
    """Return full-day distance between two timestamps in UTC."""
    then_utc = then.astimezone(UTC) if then.tzinfo is not None else then.replace(tzinfo=UTC)
    return max(0, int((now.astimezone(UTC) - then_utc).days))


def _stable_json(data: Any) -> str:
    """Return deterministic JSON suitable for finding dimensions."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _rule_is_enabled(rule: Mapping[str, Any]) -> bool:
    """Return True when an S3 lifecycle or replication rule is enabled."""
    status = str(rule.get("Status") or "").strip().lower()
    return status in {"", "enabled"}


def _stringify_number(value: float) -> str:
    """Render a float deterministically for dimensions."""
    return f"{float(value):.6f}"


def _scope(
    ctx: Any,
    *,
    account_id: str,
    billing_account_id: str,
    region: str,
    bucket: str,
) -> Scope:
    return build_scope(
        ctx,
        account=AwsAccountContext(account_id=str(account_id), billing_account_id=str(billing_account_id)),
        region=str(region),
        service="AmazonS3",
        resource_type="s3_bucket",
        resource_id=str(bucket),
        resource_arn=f"arn:aws:s3:::{bucket}",
    )


class S3StorageChecker:
    """Consolidated S3 checker (governance + storage optimization insight)."""

    checker_id = "aws.s3.storage"  # informational; emitted findings use per-signal check_id
    is_regional = False

    # check ids
    _CID_LIFECYCLE = "aws.s3.governance.lifecycle.missing"
    _CID_ENCRYPTION = "aws.s3.governance.encryption.missing"
    _CID_PAB = "aws.s3.governance.public.access.block.missing"
    _CID_COST = "aws.s3.cost.bucket.storage.estimate"
    _CID_LIFECYCLE_REVIEW = "aws.s3.cost.lifecycle.transition.review"
    _CID_MULTIPART = "aws.s3.cost.multipart.upload.cleanup"
    _CID_REPLICATION = "aws.s3.cost.replication.review"
    _CID_TIERING = "aws.s3.cost.intelligent_tiering.review"

    _STORAGE_MATRIX: tuple[tuple[str, str, float], ...] = (
        ("StandardStorage", "Standard", S3_DEFAULT_STORAGE_PRICE_GB_MONTH_USD),
        ("StandardIAStorage", "Standard - Infrequent Access", 0.0125),
        ("OneZoneIAStorage", "One Zone - Infrequent Access", 0.0100),
        ("IntelligentTieringFAStorage", "Intelligent-Tiering Frequent Access", S3_DEFAULT_STORAGE_PRICE_GB_MONTH_USD),
        ("IntelligentTieringIAStorage", "Intelligent-Tiering Infrequent Access", 0.0125),
        ("IntelligentTieringAAStorage", "Intelligent-Tiering Archive Access", 0.0040),
        ("GlacierStorage", "Glacier Flexible Retrieval", 0.0040),
        ("GlacierIRStorage", "Glacier Instant Retrieval", 0.0050),
        ("DeepArchiveStorage", "Glacier Deep Archive", 0.00099),
    )

    def __init__(
        self,
        *,
        account: AwsAccountContext,
        default_storage_price_gb_month_usd: float = S3_DEFAULT_STORAGE_PRICE_GB_MONTH_USD,
        metric_lookback_days: int = S3_METRIC_LOOKBACK_DAYS,
    ) -> None:
        """Initialize the checker with account context and pricing defaults."""
        self._account = account
        self._default_price = float(default_storage_price_gb_month_usd)
        self._lookback_days = int(metric_lookback_days)
        self._storage_price_cache: dict[tuple[str, str], tuple[float, str, int, str]] = {}

    def run(self, ctx: RunContext) -> Iterable[FindingDraft]:
        """Execute the checker and yield bucket-scoped findings."""
        _LOGGER.info("Starting S3 storage check")
        if ctx.services is None:
            raise RuntimeError("S3StorageChecker requires ctx.services (AWS clients)")

        s3: BaseClient = ctx.services.s3
        cloudwatch: BaseClient | None = getattr(ctx.services, "cloudwatch", None)
        pricing = getattr(ctx.services, "pricing", None)

        billing_account_id = self._account.billing_account_id or self._account.account_id

        resp = s3.list_buckets()
        _LOGGER.debug("Listed S3 buckets")
        bucket_count = len(resp.get("Buckets", []) or [])
        _LOGGER.info("S3 buckets found", extra={"bucket_count": bucket_count})
        for bucket in resp.get("Buckets", []) or []:
            name = str(bucket.get("Name") or "")
            if not name:
                continue

            bucket_region = self._bucket_region_best_effort(s3, name)
            scope = _scope(
                ctx,
                account_id=self._account.account_id,
                billing_account_id=billing_account_id,
                region=bucket_region,
                bucket=name,
            )
            lifecycle_state, lifecycle_note, lifecycle_rules = self._lifecycle_state_best_effort(s3, name)
            lifecycle_analysis = self._analyze_lifecycle_rules(lifecycle_rules)

            # ------------------------------
            # Governance: lifecycle
            # ------------------------------
            if lifecycle_state == "missing":
                yield FindingDraft(
                    check_id=self._CID_LIFECYCLE,
                    check_name="S3 bucket missing lifecycle policy",
                    category="governance",
                    status="fail",
                    severity=Severity(level="medium", score=50),
                    title="S3 bucket has no lifecycle configuration",
                    message=f"Bucket {name} does not have a lifecycle policy.",
                    recommendation="Add lifecycle rules to transition or expire objects where appropriate.",
                    scope=scope,
                    issue_key={"check_id": self._CID_LIFECYCLE, "bucket": name},
                    estimated_monthly_savings=None,
                    estimate_confidence=0,
                    estimate_notes=lifecycle_note,
                )
            elif lifecycle_state == "unknown":
                yield FindingDraft(
                    check_id=self._CID_LIFECYCLE,
                    check_name="S3 lifecycle policy missing (unable to verify)",
                    category="governance",
                    status="info",
                    severity=Severity(level="low", score=10),
                    title="Cannot verify lifecycle policy (access denied)",
                    message=f"Access denied when reading lifecycle configuration for bucket {name}.",
                    recommendation="Grant s3:GetLifecycleConfiguration to the scanner role.",
                    scope=scope,
                    issue_key={"check_id": self._CID_LIFECYCLE, "bucket": name, "reason": "access_denied"},
                    estimated_monthly_savings=None,
                    estimate_confidence=0,
                    estimate_notes=lifecycle_note,
                )

            # ------------------------------
            # Governance: default encryption
            # ------------------------------
            enc_state, enc_note = self._has_default_encryption_best_effort(s3, name)
            if enc_state == "missing":
                yield FindingDraft(
                    check_id=self._CID_ENCRYPTION,
                    check_name="S3 bucket missing default encryption",
                    category="governance",
                    status="fail",
                    severity=Severity(level="high", score=80),
                    title="S3 bucket has no default encryption",
                    message=f"Bucket {name} has no default encryption configured (SSE).",
                    recommendation="Enable default encryption (SSE-S3 or SSE-KMS) for the bucket.",
                    scope=scope,
                    issue_key={"check_id": self._CID_ENCRYPTION, "bucket": name},
                    estimated_monthly_savings=None,
                    estimate_confidence=0,
                    estimate_notes=enc_note,
                )
            elif enc_state == "unknown":
                yield FindingDraft(
                    check_id=self._CID_ENCRYPTION,
                    check_name="S3 default encryption missing (unable to verify)",
                    category="governance",
                    status="info",
                    severity=Severity(level="low", score=10),
                    title="Cannot verify default encryption (access denied)",
                    message=f"Access denied when reading encryption configuration for bucket {name}.",
                    recommendation="Grant s3:GetEncryptionConfiguration to the scanner role.",
                    scope=scope,
                    issue_key={"check_id": self._CID_ENCRYPTION, "bucket": name, "reason": "access_denied"},
                    estimated_monthly_savings=None,
                    estimate_confidence=0,
                    estimate_notes=enc_note,
                )

            # ------------------------------
            # Governance: Public Access Block
            # ------------------------------
            pab_state, pab_note = self._public_access_block_state_best_effort(s3, name)
            if pab_state == "missing":
                yield FindingDraft(
                    check_id=self._CID_PAB,
                    check_name="S3 bucket missing Public Access Block",
                    category="governance",
                    status="fail",
                    severity=Severity(level="high", score=85),
                    title="S3 bucket public access block is missing/disabled",
                    message=(
                        f"Bucket {name} does not have a Public Access Block configuration, "
                        "or it is not fully enabled."
                    ),
                    recommendation="Enable S3 Public Access Block (all 4 settings) unless explicitly required.",
                    scope=scope,
                    issue_key={"check_id": self._CID_PAB, "bucket": name},
                    estimated_monthly_savings=None,
                    estimate_confidence=0,
                    estimate_notes=pab_note,
                )
            elif pab_state == "unknown":
                yield FindingDraft(
                    check_id=self._CID_PAB,
                    check_name="S3 Public Access Block missing (unable to verify)",
                    category="governance",
                    status="info",
                    severity=Severity(level="low", score=10),
                    title="Cannot verify Public Access Block (access denied)",
                    message=f"Access denied when reading Public Access Block for bucket {name}.",
                    recommendation="Grant s3:GetBucketPublicAccessBlock to the scanner role.",
                    scope=scope,
                    issue_key={"check_id": self._CID_PAB, "bucket": name, "reason": "access_denied"},
                    estimated_monthly_savings=None,
                    estimate_confidence=0,
                    estimate_notes=pab_note,
                )

            # ------------------------------
            # Cost: storage estimate (multi-class, best-effort)
            # ------------------------------
            storage_metrics: dict[str, Any] | None = None
            breakdown: dict[str, Any] | None = None
            if cloudwatch is not None:
                storage_metrics = self._bucket_storage_metrics_best_effort(cloudwatch, bucket=name)
                breakdown = self._bucket_storage_breakdown_best_effort(
                    storage_metrics,
                    pricing=pricing,
                    region=bucket_region,
                )
                if breakdown is not None:
                    total_gib = float(breakdown.get("total_size_gib") or 0.0)

                    # Guard against "0.0 GiB" findings caused by rounding tiny non-zero values.
                    # CloudWatch S3 metrics can be sparse and/or small buckets may round to 0.0 at 1 decimal.
                    # If you want *any* storage estimate finding, it should at least be visibly > 0.0.
                    if total_gib < 0.05:  # ~51 MiB
                        continue

                    # Your policy: don't emit small buckets (noise). Keep as-is.
                    if total_gib < 10.0:
                        continue

                    total_cost = float(breakdown.get("total_monthly_cost_usd") or 0.0)

                    # Deterministic JSON breakdown (stable key ordering, stable rounding)
                    breakdown_items = breakdown.get("items") or []
                    classes = len(breakdown_items)
                    class_word = "class" if classes == 1 else "classes"
                    across_phrase = f"across {classes} {class_word}"

                    breakdown_json = _stable_json(breakdown_items)

                    yield FindingDraft(
                        check_id=self._CID_COST,
                        check_name="S3 bucket storage cost estimate",
                        category="cost",
                        sub_category="storage",
                        status="info",
                        severity=Severity(level="low", score=20),
                        title=(
                            f"S3 bucket storage estimate: {name} "
                            f"(~{total_gib:.1f} GiB {across_phrase})"
                        ),
                        message=(
                            f"Estimated storage size ~ {total_gib:.1f} GiB {across_phrase}. "
                            f"Estimated cost ~ ${total_cost:.2f}/month (storage-only)."
                        ),
                        recommendation=(
                            "Use this estimate to prioritize storage optimization (lifecycle, tiering, archival). "
                            "Confirm with AWS Cost Explorer / CUR for billing-accurate numbers."
                        ),
                        scope=scope,
                        issue_key={"check_id": self._CID_COST, "bucket": name, "mode": "multi_class"},
                        estimated_monthly_cost=round(total_cost, 2),
                        estimated_monthly_savings=None,
                        estimate_confidence=int(breakdown.get("estimate_confidence") or 0),
                        estimate_notes=str(breakdown.get("estimate_notes") or ""),
                        dimensions={
                            "currency": "USD",
                            "total_size_gib": f"{total_gib:.4f}",
                            "total_monthly_cost_usd": f"{total_cost:.4f}",
                            "breakdown_json": breakdown_json,
                        },
                    )
                    yield from self._emit_storage_optimization_findings(
                        s3=s3,
                        scope=scope,
                        bucket=name,
                        breakdown=breakdown,
                        storage_metrics=storage_metrics,
                        lifecycle_state=lifecycle_state,
                        lifecycle_analysis=lifecycle_analysis,
                    )


    # ------------------------------
    # Best-effort helpers
    # ------------------------------

    def _bucket_region_best_effort(self, s3: BaseClient, bucket: str) -> str:
        try:
            loc = s3.get_bucket_location(Bucket=bucket)
            return _normalize_s3_location_constraint(loc.get("LocationConstraint"))
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in ("AccessDenied", "AllAccessDisabled"):
                return "unknown"
            raise

    def _lifecycle_state_best_effort(
        self,
        s3: BaseClient,
        bucket: str,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """Return lifecycle state, note, and lifecycle rules for a bucket."""
        try:
            resp = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
            rules = [rule for rule in (resp.get("Rules", []) or []) if isinstance(rule, dict)]
            return "present", "", rules
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in ("NoSuchLifecycleConfiguration", "NoSuchLifecycleConfigurationException"):
                return "missing", "No lifecycle configuration.", []
            if code in ("AccessDenied", "AllAccessDisabled"):
                return "unknown", "Access denied while reading lifecycle configuration.", []
            raise

    def _analyze_lifecycle_rules(self, rules: list[dict[str, Any]]) -> dict[str, bool]:
        """Summarize whether lifecycle rules already cover key storage controls."""
        has_transition = False
        has_abort_incomplete = False
        for rule in rules:
            if not _rule_is_enabled(rule):
                continue
            if isinstance(rule.get("Transitions"), list) and rule.get("Transitions"):
                has_transition = True
            if isinstance(rule.get("NoncurrentVersionTransitions"), list) and rule.get("NoncurrentVersionTransitions"):
                has_transition = True
            abort_cfg = rule.get("AbortIncompleteMultipartUpload")
            if isinstance(abort_cfg, Mapping):
                days_after = abort_cfg.get("DaysAfterInitiation")
                if isinstance(days_after, (int, float)) and float(days_after) > 0:
                    has_abort_incomplete = True
        return {
            "has_transition": has_transition,
            "has_abort_incomplete": has_abort_incomplete,
        }

    def _has_default_encryption_best_effort(self, s3: BaseClient, bucket: str) -> tuple[str, str]:
        try:
            s3.get_bucket_encryption(Bucket=bucket)
            return "present", ""
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in ("ServerSideEncryptionConfigurationNotFoundError", "NoSuchEncryptionConfiguration"):
                return "missing", "No default encryption configuration."
            if code in ("AccessDenied", "AllAccessDisabled"):
                return "unknown", "Access denied while reading encryption configuration."
            raise

    def _public_access_block_state_best_effort(self, s3: BaseClient, bucket: str) -> tuple[str, str]:
        try:
            resp = s3.get_public_access_block(Bucket=bucket)
            cfg = (resp or {}).get("PublicAccessBlockConfiguration") or {}
            required = [
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            ]
            if all(bool(cfg.get(k)) for k in required):
                return "present", ""
            return "missing", "Public Access Block is not fully enabled."
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in ("NoSuchPublicAccessBlockConfiguration", "NoSuchPublicAccessBlockConfigurationException"):
                return "missing", "No Public Access Block configuration."
            if code in ("AccessDenied", "AllAccessDisabled"):
                return "unknown", "Access denied while reading Public Access Block configuration."
            raise

    # ------------------------------
    # CloudWatch sizing helpers
    # ------------------------------
    def _bucket_storage_metrics_best_effort(
        self,
        cloudwatch: BaseClient,
        *,
        bucket: str,
    ) -> dict[str, Any] | None:
        """Return batched CloudWatch S3 storage metrics for a bucket."""
        end = now_utc()
        start = end - timedelta(days=max(1, self._lookback_days))
        storage_types = [item[0] for item in self._STORAGE_MATRIX]
        queries: list[dict[str, Any]] = []
        query_meta: dict[str, tuple[str, str]] = {}
        for index, storage_type in enumerate(storage_types):
            query_id = f"q{index}"
            query_meta[query_id] = ("BucketSizeBytes", storage_type)
            queries.append(
                {
                    "Id": query_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/S3",
                            "MetricName": "BucketSizeBytes",
                            "Dimensions": [
                                {"Name": "BucketName", "Value": bucket},
                                {"Name": "StorageType", "Value": storage_type},
                            ],
                        },
                        "Period": 86400,
                        "Stat": "Average",
                    },
                    "ReturnData": True,
                }
            )
        query_meta["objects"] = ("NumberOfObjects", "AllStorageTypes")
        queries.append(
            {
                "Id": "objects",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/S3",
                        "MetricName": "NumberOfObjects",
                        "Dimensions": [
                            {"Name": "BucketName", "Value": bucket},
                            {"Name": "StorageType", "Value": "AllStorageTypes"},
                        ],
                    },
                    "Period": 86400,
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        )
        try:
            resp = cloudwatch.get_metric_data(
                MetricDataQueries=queries,
                StartTime=start,
                EndTime=end,
                ScanBy="TimestampDescending",
            )
        except (AttributeError, ClientError, TypeError, ValueError):
            return None
        results = resp.get("MetricDataResults", []) or []
        sizes_gib: dict[str, float] = {}
        object_count: float | None = None
        for result in results:
            if not isinstance(result, dict):
                continue
            result_id = str(result.get("Id") or "")
            metric_name, storage_type = query_meta.get(result_id, ("", ""))
            values = result.get("Values", []) or []
            if not values:
                continue
            try:
                latest_value = float(values[0])
            except (TypeError, ValueError):
                continue
            if metric_name == "BucketSizeBytes":
                sizes_gib[storage_type] = 0.0 if latest_value <= 0 else _bytes_to_gib(latest_value)
            elif metric_name == "NumberOfObjects":
                object_count = max(0.0, latest_value)
        if not sizes_gib and object_count is None:
            return None
        return {
            "sizes_gib": sizes_gib,
            "object_count": object_count,
        }

    # ------------------------------
    # Pricing helpers (best-effort)
    # ------------------------------
    def _storage_price_best_effort(
        self,
        *,
        pricing: Any,
        region: str,
        pricing_storage_class: str,
        fallback_usd_per_gb_month: float,
    ) -> tuple[float, str, int, str]:
        """Return (usd_per_gb_month, notes, confidence, price_source)."""
        cache_key = (str(region or ""), str(pricing_storage_class or ""))
        cached = self._storage_price_cache.get(cache_key)
        if cached is not None:
            return cached
        pricing_ctx = SimpleNamespace(services=SimpleNamespace(pricing=pricing))
        resolved = PricingResolver(pricing_ctx).resolve_s3_storage_price(
            region=region,
            pricing_storage_class=pricing_storage_class,
            fallback_usd_per_gb_month=fallback_usd_per_gb_month,
            call_exceptions=(AttributeError, TypeError, ValueError, ClientError),
        )
        self._storage_price_cache[cache_key] = resolved
        return resolved

    def _bucket_storage_breakdown_best_effort(
        self,
        storage_metrics: dict[str, Any] | None,
        *,
        pricing: Any,
        region: str,
    ) -> dict[str, Any] | None:
        """Compute a deterministic multi-class storage breakdown for a bucket.

        Uses CloudWatch storage metrics for multiple storage classes and estimates cost
        using PricingService when possible (fallback otherwise).
        """
        if storage_metrics is None:
            return None
        sizes_gib = storage_metrics.get("sizes_gib")
        if not isinstance(sizes_gib, Mapping):
            return None

        items: list[dict[str, str]] = []
        total_size = 0.0
        total_cost = 0.0
        confidences: list[int] = []
        notes_parts: list[str] = []

        for cw_storage_type, pricing_storage_class, fallback_price in self._STORAGE_MATRIX:
            raw_size = sizes_gib.get(cw_storage_type)
            try:
                size_gib = float(raw_size)
            except (TypeError, ValueError):
                size_gib = None
            if size_gib is None or size_gib <= 0:
                continue

            usd_per_gb_month, note, conf, source = self._storage_price_best_effort(
                pricing=pricing,
                region=region,
                pricing_storage_class=pricing_storage_class,
                fallback_usd_per_gb_month=fallback_price,
            )
            monthly_cost = float(size_gib) * float(usd_per_gb_month)

            items.append(
                {
                    "storage_type": cw_storage_type,
                    "pricing_storage_class": pricing_storage_class,
                    "size_gib": f"{float(size_gib):.6f}",
                    "usd_per_gb_month": f"{float(usd_per_gb_month):.6f}",
                    "monthly_cost_usd": f"{float(monthly_cost):.6f}",
                    "price_source": source,
                }
            )
            total_size += float(size_gib)
            total_cost += float(monthly_cost)
            confidences.append(int(conf))
            notes_parts.append(note)

        if not items:
            return None

        est_conf = min(confidences) if confidences else 50
        uniq_notes: list[str] = []
        for n in notes_parts:
            if n and n not in uniq_notes:
                uniq_notes.append(n)
        summary_notes = "; ".join(uniq_notes[:3])

        return {
            "items": items,
            "total_size_gib": float(total_size),
            "total_monthly_cost_usd": float(total_cost),
            "estimate_confidence": int(est_conf),
            "estimate_notes": summary_notes,
            "object_count": storage_metrics.get("object_count"),
        }

    def _multipart_upload_cleanup_candidate_best_effort(
        self,
        s3: BaseClient,
        *,
        bucket: str,
        has_abort_rule: bool,
    ) -> dict[str, Any] | None:
        """Return stale multipart upload summary when cleanup looks missing."""
        if has_abort_rule:
            return None
        try:
            resp = s3.list_multipart_uploads(Bucket=bucket, MaxUploads=25)
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in ("AccessDenied", "AllAccessDisabled", "NoSuchBucket"):
                return None
            raise
        uploads = resp.get("Uploads", []) or []
        if not uploads:
            return None
        now = now_utc()
        stale_days: list[int] = []
        for upload in uploads:
            if not isinstance(upload, Mapping):
                continue
            initiated = upload.get("Initiated")
            if not isinstance(initiated, datetime):
                continue
            age_days = _days_between(now, initiated)
            if age_days >= 7:
                stale_days.append(age_days)
        if not stale_days:
            return None
        stale_days.sort(reverse=True)
        return {
            "stale_upload_count": len(stale_days),
            "oldest_upload_age_days": stale_days[0],
        }

    def _replication_review_best_effort(self, s3: BaseClient, *, bucket: str) -> dict[str, Any] | None:
        """Return a summary of enabled replication rules for a bucket."""
        try:
            resp = s3.get_bucket_replication(Bucket=bucket)
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in (
                "ReplicationConfigurationNotFoundError",
                "NoSuchReplicationConfiguration",
                "AccessDenied",
                "AllAccessDisabled",
            ):
                return None
            raise
        rules = [rule for rule in (resp.get("ReplicationConfiguration", {}).get("Rules", []) or []) if isinstance(rule, dict)]
        enabled_rules = [rule for rule in rules if _rule_is_enabled(rule)]
        if not enabled_rules:
            return None
        destinations: list[dict[str, str]] = []
        for rule in enabled_rules:
            dest = rule.get("Destination")
            if not isinstance(dest, Mapping):
                continue
            destinations.append(
                {
                    "bucket": str(dest.get("Bucket") or ""),
                    "storage_class": str(dest.get("StorageClass") or "same_as_source"),
                }
            )
        return {
            "enabled_rule_count": len(enabled_rules),
            "destinations": destinations,
        }

    def _intelligent_tiering_configured_best_effort(self, s3: BaseClient, *, bucket: str) -> bool:
        """Return True when the bucket has at least one Intelligent-Tiering config."""
        try:
            resp = s3.list_bucket_intelligent_tiering_configurations(Bucket=bucket)
        except (AttributeError, TypeError, ValueError):
            return False
        except ClientError as exc:
            code = _client_error_code(exc)
            if code in ("AccessDenied", "AllAccessDisabled", "NoSuchConfiguration"):
                return False
            raise
        configs = resp.get("IntelligentTieringConfigurationList", []) or []
        return any(isinstance(item, Mapping) for item in configs)

    def _emit_storage_optimization_findings(
        self,
        *,
        s3: BaseClient,
        scope: Scope,
        bucket: str,
        breakdown: dict[str, Any],
        storage_metrics: dict[str, Any] | None,
        lifecycle_state: str,
        lifecycle_analysis: dict[str, bool],
    ) -> Iterable[FindingDraft]:
        """Yield additional S3 optimization findings derived from storage shape."""
        total_gib = float(breakdown.get("total_size_gib") or 0.0)
        total_cost = float(breakdown.get("total_monthly_cost_usd") or 0.0)
        object_count = storage_metrics.get("object_count") if isinstance(storage_metrics, Mapping) else None
        standard_gib = 0.0
        intelligent_tiering_gib = 0.0
        for item in breakdown.get("items", []) or []:
            if not isinstance(item, Mapping):
                continue
            storage_type = str(item.get("storage_type") or "")
            try:
                size_gib = float(item.get("size_gib") or 0.0)
            except (TypeError, ValueError):
                size_gib = 0.0
            if storage_type == "StandardStorage":
                standard_gib += size_gib
            if storage_type.startswith("IntelligentTiering"):
                intelligent_tiering_gib += size_gib
        standard_share = (standard_gib / total_gib) if total_gib > 0 else 0.0
        has_transition = bool(lifecycle_analysis.get("has_transition"))
        has_abort_rule = bool(lifecycle_analysis.get("has_abort_incomplete"))
        has_intelligent_tiering = intelligent_tiering_gib > 0.0 or self._intelligent_tiering_configured_best_effort(
            s3, bucket=bucket
        )

        if total_gib >= 128.0 and standard_share >= 0.80 and lifecycle_state != "unknown" and not has_transition:
            yield FindingDraft(
                check_id=self._CID_LIFECYCLE_REVIEW,
                check_name="S3 lifecycle transition review",
                category="cost",
                sub_category="storage",
                status="info",
                severity=Severity(level="medium", score=45),
                title=f"S3 bucket may benefit from storage class transitions: {bucket}",
                message=(
                    f"Bucket {bucket} stores about {total_gib:.1f} GiB with {standard_share:.0%} in Standard storage "
                    "and no enabled lifecycle transition rules were detected."
                ),
                recommendation=(
                    "Review prefix-level lifecycle transitions for colder data, including Standard-IA, "
                    "Intelligent-Tiering, Glacier Instant Retrieval, or archive tiers where retrieval patterns allow it."
                ),
                scope=scope,
                issue_key={"check_id": self._CID_LIFECYCLE_REVIEW, "bucket": bucket},
                estimate_confidence=55,
                estimate_notes="Inference from bucket storage-class mix and lifecycle configuration.",
                dimensions={
                    "currency": "USD",
                    "total_size_gib": f"{total_gib:.4f}",
                    "standard_storage_gib": f"{standard_gib:.4f}",
                    "standard_storage_share": _stringify_number(standard_share),
                },
            )

        multipart = self._multipart_upload_cleanup_candidate_best_effort(
            s3,
            bucket=bucket,
            has_abort_rule=has_abort_rule,
        )
        if multipart is not None:
            yield FindingDraft(
                check_id=self._CID_MULTIPART,
                check_name="S3 multipart upload cleanup review",
                category="cost",
                sub_category="storage",
                status="info",
                severity=Severity(level="medium", score=40),
                title=f"S3 bucket has stale multipart uploads without cleanup: {bucket}",
                message=(
                    f"Bucket {bucket} has {multipart['stale_upload_count']} multipart upload(s) at least 7 days old, "
                    "and no lifecycle abort rule was detected."
                ),
                recommendation=(
                    "Add an AbortIncompleteMultipartUpload lifecycle rule and review abandoned upload producers."
                ),
                scope=scope,
                issue_key={"check_id": self._CID_MULTIPART, "bucket": bucket},
                estimate_confidence=65,
                estimate_notes="Multipart upload sizes are not exposed here, so savings are unquantified.",
                dimensions={
                    "stale_upload_count": str(multipart["stale_upload_count"]),
                    "oldest_upload_age_days": str(multipart["oldest_upload_age_days"]),
                },
            )

        replication = self._replication_review_best_effort(s3, bucket=bucket)
        if replication is not None and total_gib >= 100.0:
            replicated_monthly_cost = total_cost
            yield FindingDraft(
                check_id=self._CID_REPLICATION,
                check_name="S3 replication cost review",
                category="cost",
                sub_category="storage",
                status="info",
                severity=Severity(level="medium", score=50),
                title=f"S3 replication may materially increase storage spend: {bucket}",
                message=(
                    f"Bucket {bucket} has {replication['enabled_rule_count']} enabled replication rule(s) and stores "
                    f"about {total_gib:.1f} GiB. Replication can duplicate storage charges and may add transfer cost."
                ),
                recommendation=(
                    "Review whether all replicated prefixes still need replication, and whether destination storage "
                    "classes and replication scope are intentionally optimized."
                ),
                scope=scope,
                issue_key={"check_id": self._CID_REPLICATION, "bucket": bucket},
                estimated_monthly_cost=round(replicated_monthly_cost, 2),
                estimate_confidence=40,
                estimate_notes=(
                    "Estimated monthly cost reflects source-bucket storage only; replicated cost may be lower or higher "
                    "depending on rule scope, destination class, and cross-region transfer."
                ),
                dimensions={
                    "currency": "USD",
                    "replication_rule_count": str(replication["enabled_rule_count"]),
                    "total_size_gib": f"{total_gib:.4f}",
                    "source_storage_cost_usd": f"{total_cost:.4f}",
                    "replication_destinations_json": _stable_json(replication["destinations"]),
                },
            )

        if total_gib >= 256.0 and standard_share >= 0.90 and not has_intelligent_tiering:
            object_text = ""
            dimensions = {
                "currency": "USD",
                "total_size_gib": f"{total_gib:.4f}",
                "standard_storage_share": _stringify_number(standard_share),
            }
            if isinstance(object_count, (int, float)):
                object_text = f" across about {int(object_count)} objects"
                dimensions["object_count"] = str(int(object_count))
            yield FindingDraft(
                check_id=self._CID_TIERING,
                check_name="S3 Intelligent-Tiering review",
                category="cost",
                sub_category="storage",
                status="info",
                severity=Severity(level="medium", score=45),
                title=f"S3 bucket may benefit from Intelligent-Tiering review: {bucket}",
                message=(
                    f"Bucket {bucket} stores about {total_gib:.1f} GiB{object_text} with little or no existing "
                    "Intelligent-Tiering usage detected."
                ),
                recommendation=(
                    "Review whether low-access prefixes or uncertain access patterns should move to Intelligent-Tiering "
                    "instead of remaining mostly in Standard storage."
                ),
                scope=scope,
                issue_key={"check_id": self._CID_TIERING, "bucket": bucket},
                estimate_confidence=50,
                estimate_notes=(
                    "This is an inference from storage-class mix, not a direct measurement of per-object access frequency."
                ),
                dimensions=dimensions,
            )


@register_checker("checks.aws.s3_storage:S3StorageChecker")
def _factory(ctx: RunContext, bootstrap: Bootstrap) -> S3StorageChecker:
    """Instantiate this checker from runtime bootstrap data."""
    account_id = str(bootstrap.get("aws_account_id") or "")
    if not account_id:
        raise RuntimeError("aws_account_id missing from bootstrap (required for S3StorageChecker)")

    billing_account_id = str(bootstrap.get("aws_billing_account_id") or account_id)
    return S3StorageChecker(
        account=AwsAccountContext(account_id=account_id, billing_account_id=billing_account_id),
    )
