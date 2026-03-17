"""Unit tests for ECR image hygiene checker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Optional, cast

from botocore.exceptions import ClientError

from checks.aws.ecr_images import EcrImagesChecker
from contracts.finops_checker_pattern import RunContext


class FakePaginator:
    """Minimal paginator fake for ECR tests."""

    def __init__(self, pages: list[Mapping[str, Any]]) -> None:
        self._pages = pages

    def paginate(self, **_kwargs: Any) -> Iterable[Mapping[str, Any]]:
        yield from self._pages


class FakeECR:
    """Minimal ECR fake for checker tests."""

    def __init__(
        self,
        *,
        region: str,
        repositories: list[Mapping[str, Any]],
        images_by_repository: dict[str, list[Mapping[str, Any]]],
        raise_on: Optional[str] = None,
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self._repositories = repositories
        self._images_by_repository = images_by_repository
        self._raise_on = raise_on

    def get_paginator(self, op_name: str) -> FakePaginator:
        if self._raise_on == op_name:
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                op_name,
            )
        if op_name == "describe_repositories":
            return FakePaginator([{"repositories": list(self._repositories)}])
        if op_name == "describe_images":
            raise KeyError(op_name)
        raise KeyError(op_name)

    def describe_repositories(self, **_kwargs: Any) -> Mapping[str, Any]:
        if self._raise_on == "describe_repositories":
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "describe_repositories",
            )
        return {"repositories": list(self._repositories)}

    def describe_images(self, *, repositoryName: str, **_kwargs: Any) -> Mapping[str, Any]:
        if self._raise_on == "describe_images":
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                "describe_images",
            )
        return {"imageDetails": list(self._images_by_repository.get(repositoryName, []))}


@dataclass
class _Services:
    ecr: Any = None
    region: str = ""


def _mk_ctx(*, ecr: Any, region: str = "eu-west-1") -> RunContext:
    return cast(
        RunContext,
        SimpleNamespace(
            cloud="aws",
            run_ts=datetime(2026, 3, 17, tzinfo=UTC),
            services=_Services(ecr=ecr, region=region),
        ),
    )


def _checker() -> EcrImagesChecker:
    import checks.aws.ecr_images as mod

    return EcrImagesChecker(
        account=mod.AwsAccountContext(account_id="111111111111", billing_account_id="111111111111")
    )


def test_stale_ecr_image_emits() -> None:
    now = datetime(2026, 3, 17, tzinfo=UTC)
    repo_arn = "arn:aws:ecr:eu-west-1:111111111111:repository/app"
    ecr = FakeECR(
        region="eu-west-1",
        repositories=[{"repositoryName": "app", "repositoryArn": repo_arn}],
        images_by_repository={
            "app": [
                {
                    "imageDigest": "sha256:abc",
                    "imageTags": ["build-1"],
                    "imagePushedAt": now - timedelta(days=120),
                    "lastRecordedPullTime": now - timedelta(days=95),
                    "imageSizeInBytes": 123456,
                }
            ]
        },
    )

    findings = list(_checker().run(_mk_ctx(ecr=ecr)))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.check_id == "aws.ecr.images.stale"
    assert finding.scope.resource_type == "image"
    assert (finding.dimensions or {}).get("repository_name") == "app"
    assert (finding.dimensions or {}).get("last_pull_age_days") == "95"


def test_recently_pulled_old_ecr_image_is_suppressed() -> None:
    now = datetime(2026, 3, 17, tzinfo=UTC)
    ecr = FakeECR(
        region="eu-west-1",
        repositories=[{"repositoryName": "app", "repositoryArn": "arn:aws:ecr:eu-west-1:111111111111:repository/app"}],
        images_by_repository={
            "app": [
                {
                    "imageDigest": "sha256:def",
                    "imageTags": ["stable"],
                    "imagePushedAt": now - timedelta(days=180),
                    "lastRecordedPullTime": now - timedelta(days=7),
                }
            ]
        },
    )

    findings = list(_checker().run(_mk_ctx(ecr=ecr)))
    assert findings == []


def test_access_denied_emits_access_error() -> None:
    ecr = FakeECR(
        region="eu-west-1",
        repositories=[],
        images_by_repository={},
        raise_on="describe_repositories",
    )
    findings = list(_checker().run(_mk_ctx(ecr=ecr)))
    assert len(findings) == 1
    assert findings[0].check_id == "aws.ecr.access.error"
    assert findings[0].status == "info"
