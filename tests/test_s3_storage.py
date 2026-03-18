# tests/test_s3_storage.py
"""Unit tests for checks.aws.s3_storage.

These tests use minimal fake clients (no boto3).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import pytest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError

from checks.aws.s3_storage import AwsAccountContext, S3StorageChecker


def _ce(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class _FakeS3:
    def __init__(
        self,
        *,
        buckets: List[str],
        location_by_bucket: Optional[Dict[str, Optional[str]]] = None,
        lifecycle_present: Optional[Dict[str, bool]] = None,
        lifecycle_rules_by_bucket: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        encryption_present: Optional[Dict[str, bool]] = None,
        pab_config_by_bucket: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
        multipart_uploads_by_bucket: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        replication_rules_by_bucket: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        intelligent_tiering_by_bucket: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self._buckets = buckets
        self._location_by_bucket = location_by_bucket or {}
        self._lifecycle_present = lifecycle_present or {}
        self._lifecycle_rules_by_bucket = lifecycle_rules_by_bucket or {}
        self._encryption_present = encryption_present or {}
        self._pab_config_by_bucket = pab_config_by_bucket or {}
        self._multipart_uploads_by_bucket = multipart_uploads_by_bucket or {}
        self._replication_rules_by_bucket = replication_rules_by_bucket or {}
        self._intelligent_tiering_by_bucket = intelligent_tiering_by_bucket or {}

    def list_buckets(self) -> Dict[str, Any]:
        return {"Buckets": [{"Name": b} for b in self._buckets]}

    def get_bucket_location(self, *, Bucket: str) -> Dict[str, Any]:
        # default: us-east-1 (None)
        return {"LocationConstraint": self._location_by_bucket.get(Bucket)}

    def get_bucket_lifecycle_configuration(self, *, Bucket: str) -> Dict[str, Any]:
        if Bucket in self._lifecycle_rules_by_bucket:
            return {"Rules": self._lifecycle_rules_by_bucket[Bucket]}
        if self._lifecycle_present.get(Bucket, False):
            return {"Rules": []}
        raise _ce("NoSuchLifecycleConfiguration", "GetBucketLifecycleConfiguration")

    def get_bucket_encryption(self, *, Bucket: str) -> Dict[str, Any]:
        if self._encryption_present.get(Bucket, False):
            return {"ServerSideEncryptionConfiguration": {"Rules": []}}
        raise _ce("ServerSideEncryptionConfigurationNotFoundError", "GetBucketEncryption")

    def get_public_access_block(self, *, Bucket: str) -> Dict[str, Any]:
        cfg = self._pab_config_by_bucket.get(Bucket)
        if cfg is None:
            raise _ce("NoSuchPublicAccessBlockConfiguration", "GetPublicAccessBlock")
        return {"PublicAccessBlockConfiguration": cfg}

    def list_multipart_uploads(self, *, Bucket: str, MaxUploads: int) -> Dict[str, Any]:
        uploads = self._multipart_uploads_by_bucket.get(Bucket, [])
        return {"Uploads": uploads[:MaxUploads]}

    def get_bucket_replication(self, *, Bucket: str) -> Dict[str, Any]:
        if Bucket not in self._replication_rules_by_bucket:
            raise _ce("ReplicationConfigurationNotFoundError", "GetBucketReplication")
        return {"ReplicationConfiguration": {"Rules": self._replication_rules_by_bucket[Bucket]}}

    def list_bucket_intelligent_tiering_configurations(self, *, Bucket: str) -> Dict[str, Any]:
        return {
            "IntelligentTieringConfigurationList": self._intelligent_tiering_by_bucket.get(Bucket, [])
        }



class _FakeCloudWatch:
    def __init__(self, *, avg_bytes_by_bucket_and_type: Dict[tuple, float]) -> None:
        # key: (bucket, storage_type)
        self._avg = avg_bytes_by_bucket_and_type

    def get_metric_data(self, **kwargs) -> Dict[str, Any]:
        results = []
        for query in kwargs.get("MetricDataQueries") or []:
            metric = ((query.get("MetricStat") or {}).get("Metric") or {})
            dims = metric.get("Dimensions") or []
            bucket = ""
            storage_type = ""
            for d in dims:
                if d.get("Name") == "BucketName":
                    bucket = str(d.get("Value") or "")
                if d.get("Name") == "StorageType":
                    storage_type = str(d.get("Value") or "")
            avg = self._avg.get((bucket, storage_type))
            values = [] if avg is None else [float(avg)]
            results.append(
                {
                    "Id": str(query.get("Id") or ""),
                    "Timestamps": [datetime(2026, 1, 1, tzinfo=timezone.utc)] if values else [],
                    "Values": values,
                }
            )
        return {"MetricDataResults": results}


class _FakePriceQuote:

    def __init__(self, *, unit_price_usd: float, unit: str = "GB-Mo", source: str = "cache") -> None:
        self.unit_price_usd = float(unit_price_usd)
        self.unit = unit
        self.source = source



class _FakePricing:
    def __init__(self, *, location: str, unit_price_by_storage_class: Dict[str, float]) -> None:
        self._location = location
        self._prices = {str(k): float(v) for k, v in unit_price_by_storage_class.items()}
        self.calls: List[Dict[str, Any]] = []

    def location_for_region(self, region: str) -> Optional[str]:
        _ = region
        return self._location

    def get_on_demand_unit_price(self, *, service_code: str, filters: Any, unit: str) -> Optional[_FakePriceQuote]:
        self.calls.append({"service_code": service_code, "filters": list(filters), "unit": unit})
        if service_code != "AmazonS3" or unit != "GB-Mo":
            return None

        storage_class = ""
        for f in list(filters):
            if f.get("Field") in ("storageClass", "volumeType"):
                storage_class = str(f.get("Value") or "")
                break

        if not storage_class:
            return None
        price = self._prices.get(storage_class)
        if price is None:
            return None
        return _FakePriceQuote(unit_price_usd=price, unit=unit, source="pricing_api")


@dataclass

class _FakeServices:
    s3: Any
    cloudwatch: Any = None
    pricing: Any = None


@dataclass
class _FakeCtx:
    cloud: str = "aws"
    services: Any = None


def _mk_checker() -> S3StorageChecker:
    return S3StorageChecker(account=AwsAccountContext(account_id="111111111111", billing_account_id="111111111111"))


def test_emits_lifecycle_encryption_and_pab_failures() -> None:
    checker = _mk_checker()

    s3 = _FakeS3(
        buckets=["b1"],
        location_by_bucket={"b1": "eu-west-3"},
        lifecycle_present={"b1": False},
        encryption_present={"b1": False},
        pab_config_by_bucket={"b1": None},
    )
    ctx = _FakeCtx(services=_FakeServices(s3=s3, cloudwatch=None, pricing=None))

    findings = list(checker.run(ctx))
    check_ids = sorted(f.check_id for f in findings)

    assert check_ids == sorted(
        [
            "aws.s3.governance.encryption.missing",
            "aws.s3.governance.lifecycle.missing",
            "aws.s3.governance.public.access.block.missing",
        ]
    )

    # region enrichment
    assert all(f.scope.region == "eu-west-3" for f in findings)
    # issue_key stable
    assert all(f.issue_key.get("bucket") == "b1" for f in findings)


def test_cost_estimate_uses_pricing_service_when_available() -> None:
    checker = _mk_checker()

    gib = 1024.0 ** 3
    # Sizes per storage class (GiB)
    sizes_gib = {
        ("b2", "StandardStorage"): 50.0,
        ("b2", "StandardIAStorage"): 25.0,
        ("b2", "OneZoneIAStorage"): 10.0,
        ("b2", "GlacierStorage"): 5.0,
        ("b2", "IntelligentTieringFAStorage"): 10.0,
    }
    sizes_bytes = {k: v * gib for k, v in sizes_gib.items()}

    s3 = _FakeS3(
        buckets=["b2"],
        location_by_bucket={"b2": "eu-west-3"},
        lifecycle_present={"b2": True},
        encryption_present={"b2": True},
        pab_config_by_bucket={
            "b2": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        },
    )
    cw = _FakeCloudWatch(avg_bytes_by_bucket_and_type=sizes_bytes)

    pricing = _FakePricing(
        location="EU (Paris)",
        unit_price_by_storage_class={
            "Standard": 0.02,
            "Standard - Infrequent Access": 0.01,
            "One Zone - Infrequent Access": 0.008,
            "Glacier Flexible Retrieval": 0.004,
            "Intelligent-Tiering Frequent Access": 0.02,
        },
    )

    ctx = _FakeCtx(services=_FakeServices(s3=s3, cloudwatch=cw, pricing=pricing))

    findings = list(checker.run(ctx))
    cost = [f for f in findings if f.check_id == "aws.s3.cost.bucket.storage.estimate"]
    assert len(cost) == 1

    f = cost[0]

    # Expected total: 50*0.02 + 25*0.01 + 10*0.008 + 5*0.004 + 10*0.02
    expected = (50 * 0.02) + (25 * 0.01) + (10 * 0.008) + (5 * 0.004) + (10 * 0.02)
    assert f.estimated_monthly_cost is not None
    assert f.estimated_monthly_cost == pytest.approx(expected, abs=0.01)

    # Breakdown present and deterministic JSON
    assert "breakdown_json" in (f.dimensions or {})
    breakdown = json.loads(f.dimensions["breakdown_json"])
    assert isinstance(breakdown, list)
    # Should include at least the 5 classes we provided
    storage_types = {i.get("storage_type") for i in breakdown}
    assert {
        "StandardStorage",
        "StandardIAStorage",
        "OneZoneIAStorage",
        "GlacierStorage",
        "IntelligentTieringFAStorage",
    }.issubset(storage_types)

    assert pricing.calls, "Expected PricingService to be queried"


def test_emits_storage_optimization_findings_for_large_standard_bucket() -> None:
    checker = _mk_checker()

    gib = 1024.0 ** 3
    sizes_bytes = {
        ("archive-bucket", "StandardStorage"): 500.0 * gib,
        ("archive-bucket", "AllStorageTypes"): 125000.0,
    }
    s3 = _FakeS3(
        buckets=["archive-bucket"],
        location_by_bucket={"archive-bucket": "eu-west-3"},
        lifecycle_present={"archive-bucket": False},
        encryption_present={"archive-bucket": True},
        pab_config_by_bucket={
            "archive-bucket": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        },
        multipart_uploads_by_bucket={
            "archive-bucket": [
                {"Initiated": datetime(2026, 3, 1, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 3, 5, tzinfo=timezone.utc)},
            ]
        },
        replication_rules_by_bucket={
            "archive-bucket": [
                {
                    "Status": "Enabled",
                    "Destination": {
                        "Bucket": "arn:aws:s3:::replica-archive-bucket",
                        "StorageClass": "STANDARD",
                    },
                }
            ]
        },
    )
    cw = _FakeCloudWatch(avg_bytes_by_bucket_and_type=sizes_bytes)
    ctx = _FakeCtx(services=_FakeServices(s3=s3, cloudwatch=cw, pricing=None))

    findings = list(checker.run(ctx))
    check_ids = {f.check_id for f in findings}

    assert "aws.s3.cost.bucket.storage.estimate" in check_ids
    assert "aws.s3.cost.multipart.upload.cleanup" in check_ids
    assert "aws.s3.cost.replication.review" in check_ids
    assert "aws.s3.cost.intelligent_tiering.review" in check_ids
    assert "aws.s3.cost.lifecycle.transition.review" not in check_ids

    tiering = next(f for f in findings if f.check_id == "aws.s3.cost.intelligent_tiering.review")
    assert tiering.dimensions["recommended_transition_target"] == "Intelligent-Tiering"
    assert tiering.dimensions["object_count"] == "125000"

    replication = next(f for f in findings if f.check_id == "aws.s3.cost.replication.review")
    assert replication.estimated_monthly_cost == pytest.approx(11.5, abs=0.01)

    multipart = next(f for f in findings if f.check_id == "aws.s3.cost.multipart.upload.cleanup")
    assert multipart.dimensions["stale_upload_count"] == "2"
    assert multipart.dimensions["oldest_upload_age_days"] == "17"
    assert multipart.dimensions["materiality_band"] == "baseline"

    assert replication.dimensions["replication_pattern"] == "general_replication_review"
    assert replication.dimensions["recommendation_focus"] == "general_review"


def test_emits_lifecycle_transition_review_for_large_standard_bucket_with_lower_object_count() -> None:
    checker = _mk_checker()

    gib = 1024.0 ** 3
    sizes_bytes = {
        ("transition-bucket", "StandardStorage"): 300.0 * gib,
        ("transition-bucket", "AllStorageTypes"): 9000.0,
    }
    s3 = _FakeS3(
        buckets=["transition-bucket"],
        location_by_bucket={"transition-bucket": "eu-west-3"},
        lifecycle_present={"transition-bucket": False},
        encryption_present={"transition-bucket": True},
        pab_config_by_bucket={
            "transition-bucket": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        },
    )
    cw = _FakeCloudWatch(avg_bytes_by_bucket_and_type=sizes_bytes)
    ctx = _FakeCtx(services=_FakeServices(s3=s3, cloudwatch=cw, pricing=None))

    findings = list(checker.run(ctx))
    check_ids = {f.check_id for f in findings}

    assert "aws.s3.cost.lifecycle.transition.review" in check_ids
    assert "aws.s3.cost.intelligent_tiering.review" not in check_ids

    lifecycle = next(f for f in findings if f.check_id == "aws.s3.cost.lifecycle.transition.review")
    assert lifecycle.dimensions["recommended_transition_target"] == "Standard-IA"


def test_emits_elevated_multipart_materiality_and_cold_replication_pattern() -> None:
    checker = _mk_checker()

    gib = 1024.0 ** 3
    sizes_bytes = {
        ("cold-replication-bucket", "StandardIAStorage"): 180.0 * gib,
        ("cold-replication-bucket", "GlacierStorage"): 220.0 * gib,
        ("cold-replication-bucket", "AllStorageTypes"): 20000.0,
    }
    s3 = _FakeS3(
        buckets=["cold-replication-bucket"],
        location_by_bucket={"cold-replication-bucket": "eu-west-3"},
        lifecycle_present={"cold-replication-bucket": True},
        encryption_present={"cold-replication-bucket": True},
        pab_config_by_bucket={
            "cold-replication-bucket": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        },
        multipart_uploads_by_bucket={
            "cold-replication-bucket": [
                {"Initiated": datetime(2026, 2, 1, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 2, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 3, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 4, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 5, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 6, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 7, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 8, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 9, tzinfo=timezone.utc)},
                {"Initiated": datetime(2026, 2, 10, tzinfo=timezone.utc)},
            ]
        },
        replication_rules_by_bucket={
            "cold-replication-bucket": [
                {
                    "Status": "Enabled",
                    "Destination": {
                        "Bucket": "arn:aws:s3:::replica-cold-replication-bucket",
                        "StorageClass": "STANDARD",
                    },
                }
            ]
        },
    )
    cw = _FakeCloudWatch(avg_bytes_by_bucket_and_type=sizes_bytes)
    ctx = _FakeCtx(services=_FakeServices(s3=s3, cloudwatch=cw, pricing=None))

    findings = list(checker.run(ctx))

    multipart = next(f for f in findings if f.check_id == "aws.s3.cost.multipart.upload.cleanup")
    assert multipart.dimensions["materiality_band"] == "elevated"
    assert multipart.severity.score == 55

    replication = next(f for f in findings if f.check_id == "aws.s3.cost.replication.review")
    assert replication.dimensions["replication_pattern"] == "cold_data_replication_review"
    assert replication.dimensions["recommendation_focus"] == "destination_storage_class"


def test_skips_cleanup_finding_when_abort_rule_exists() -> None:
    checker = _mk_checker()

    gib = 1024.0 ** 3
    sizes_bytes = {
        ("mpu-bucket", "StandardStorage"): 300.0 * gib,
        ("mpu-bucket", "AllStorageTypes"): 1000.0,
    }
    s3 = _FakeS3(
        buckets=["mpu-bucket"],
        location_by_bucket={"mpu-bucket": "eu-west-3"},
        lifecycle_rules_by_bucket={
            "mpu-bucket": [
                {
                    "Status": "Enabled",
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 3},
                }
            ]
        },
        encryption_present={"mpu-bucket": True},
        pab_config_by_bucket={
            "mpu-bucket": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        },
        multipart_uploads_by_bucket={
            "mpu-bucket": [
                {"Initiated": datetime(2026, 3, 1, tzinfo=timezone.utc)},
            ]
        },
    )
    cw = _FakeCloudWatch(avg_bytes_by_bucket_and_type=sizes_bytes)
    ctx = _FakeCtx(services=_FakeServices(s3=s3, cloudwatch=cw, pricing=None))

    findings = list(checker.run(ctx))

    assert "aws.s3.cost.multipart.upload.cleanup" not in {f.check_id for f in findings}
