"""Amazon ECR image hygiene checker.

Signals:
1) Stale images:
   - image push older than configured threshold
   - and no recorded pull, or last recorded pull also older than threshold
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from checks.aws._common import (
    AwsAccountContext,
    build_scope,
    get_logger,
    paginate_items,
    safe_region_from_client,
    utc,
)
from checks.aws.defaults import ECR_MAX_FINDINGS_PER_TYPE, ECR_STALE_IMAGE_DAYS
from checks.registry import Bootstrap, register_checker
from contracts.finops_checker_pattern import Checker, FindingDraft, RunContext, Severity

_LOGGER = get_logger("ecr_images")


@dataclass(frozen=True)
class EcrImagesConfig:
    """Configuration knobs for EcrImagesChecker."""

    stale_image_days: int = ECR_STALE_IMAGE_DAYS
    max_findings_per_type: int = ECR_MAX_FINDINGS_PER_TYPE


def _is_access_denied(exc: ClientError) -> bool:
    try:
        code = str(exc.response.get("Error", {}).get("Code") or "")
    except (TypeError, ValueError, AttributeError):
        return False
    return code in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
        "UnrecognizedClientException",
    }


def _age_days(value: Any, *, now_ts: datetime) -> int | None:
    dt = value if isinstance(value, datetime) else utc(value if isinstance(value, datetime) else None)
    if dt is None:
        return None
    normalized = utc(dt)
    if normalized is None:
        return None
    return int((now_ts - normalized).total_seconds() // 86400)


def _image_resource_id(repository_name: str, image_digest: str) -> str:
    digest = str(image_digest or "").strip()
    if not digest:
        return repository_name
    return f"{repository_name}@{digest}"


def _format_tags(image_tags: Any) -> str:
    if not isinstance(image_tags, list):
        return ""
    values = sorted({str(tag or "").strip() for tag in image_tags if str(tag or "").strip()})
    return ",".join(values)


class EcrImagesChecker(Checker):
    """Detect stale Amazon ECR images that may be safe to review or remove."""

    checker_id = "aws.ecr.images.audit"

    def __init__(
        self,
        *,
        account: AwsAccountContext,
        cfg: EcrImagesConfig | None = None,
    ) -> None:
        self._account = account
        self._cfg = cfg or EcrImagesConfig()

    def run(self, ctx: RunContext) -> Iterable[FindingDraft]:
        _LOGGER.info("Starting ECR image hygiene check")
        services = getattr(ctx, "services", None)
        ecr = getattr(services, "ecr", None) if services is not None else None
        if ecr is None:
            return []

        region = safe_region_from_client(ecr) or str(getattr(services, "region", "") or "")
        emitted = 0
        now_ts = utc(getattr(ctx, "run_ts", None))
        if now_ts is None:
            now_ts = utc(datetime.utcnow())
        assert now_ts is not None

        try:
            repositories = list(
                paginate_items(
                    ecr,
                    "describe_repositories",
                    "repositories",
                )
            )
        except ClientError as exc:
            if _is_access_denied(exc):
                yield self._access_error(ctx, region=region, action="ecr:DescribeRepositories", exc=exc)
                return
            raise

        for repository in repositories:
            if not isinstance(repository, Mapping):
                continue
            repository_name = str(repository.get("repositoryName") or "").strip()
            repository_arn = str(repository.get("repositoryArn") or "").strip()
            if not repository_name:
                continue

            try:
                image_details = list(
                    paginate_items(
                        ecr,
                        "describe_images",
                        "imageDetails",
                        params={"repositoryName": repository_name},
                    )
                )
            except ClientError as exc:
                if _is_access_denied(exc):
                    if emitted < self._cfg.max_findings_per_type:
                        yield self._access_error(
                            ctx,
                            region=region,
                            action="ecr:DescribeImages",
                            exc=exc,
                            repository_name=repository_name,
                        )
                    return
                raise

            for image in image_details:
                if not isinstance(image, Mapping):
                    continue
                pushed_age_days = _age_days(image.get("imagePushedAt"), now_ts=now_ts)
                if pushed_age_days is None or pushed_age_days < self._cfg.stale_image_days:
                    continue

                last_pull_age_days = _age_days(image.get("lastRecordedPullTime"), now_ts=now_ts)
                if last_pull_age_days is not None and last_pull_age_days < self._cfg.stale_image_days:
                    continue

                if emitted >= self._cfg.max_findings_per_type:
                    return
                emitted += 1

                image_digest = str(image.get("imageDigest") or "").strip()
                image_tags = _format_tags(image.get("imageTags"))
                image_size_bytes = image.get("imageSizeInBytes")
                pull_status = (
                    "never_pulled"
                    if last_pull_age_days is None
                    else f"last_pull_{last_pull_age_days}_days_ago"
                )

                yield FindingDraft(
                    check_id="aws.ecr.images.stale",
                    check_name="ECR stale image",
                    category="cost",
                    status="info",
                    severity=Severity(level="low", score=230),
                    title=f"ECR image appears stale: {_image_resource_id(repository_name, image_digest)}",
                    scope=build_scope(
                        ctx,
                        account=self._account,
                        region=region,
                        service="ecr",
                        resource_type="image",
                        resource_id=_image_resource_id(repository_name, image_digest),
                        resource_arn=repository_arn,
                    ),
                    message=(
                        f"Image in repository '{repository_name}' was pushed {pushed_age_days} days ago"
                        + (
                            " and has no recorded pull activity."
                            if last_pull_age_days is None
                            else f" and was last pulled {last_pull_age_days} days ago."
                        )
                    ),
                    recommendation=(
                        "Review whether this image is still required for rollback, compliance, or active deployments. "
                        "If not, delete it or enforce an ECR lifecycle policy to expire stale images automatically."
                    ),
                    dimensions={
                        "repository_name": repository_name,
                        "image_digest": image_digest,
                        "image_tags": image_tags,
                        "pushed_age_days": str(pushed_age_days),
                        "pull_status": pull_status,
                        "last_pull_age_days": "" if last_pull_age_days is None else str(last_pull_age_days),
                        "image_size_bytes": "" if image_size_bytes is None else str(image_size_bytes),
                    },
                    issue_key={
                        "signal": "stale_image",
                        "repository_name": repository_name,
                        "image_digest": image_digest,
                    },
                )

    def _access_error(
        self,
        ctx: RunContext,
        *,
        region: str,
        action: str,
        exc: ClientError,
        repository_name: str | None = None,
    ) -> FindingDraft:
        code = ""
        try:
            code = str(exc.response.get("Error", {}).get("Code") or "")
        except (TypeError, ValueError, AttributeError):
            code = ""
        return FindingDraft(
            check_id="aws.ecr.access.error",
            check_name="ECR API access error",
            category="governance",
            status="info",
            severity=Severity(level="info", score=0),
            title="Unable to collect full ECR inventory due to IAM restrictions",
            scope=build_scope(
                ctx,
                account=self._account,
                region=region,
                service="ecr",
                resource_type="repository",
                resource_id=repository_name or self._account.account_id,
            ),
            message=(
                f"Access denied calling {action} in region '{region}'. ErrorCode={code}"
                + (f" Repository={repository_name}." if repository_name else ".")
            ),
            recommendation=(
                "Grant least-privilege read permissions for ECR inventory APIs so the platform can identify stale images."
            ),
            issue_key={
                "signal": "access_error",
                "service": "ecr",
                "action": action,
                "region": region,
                "repository_name": repository_name or "",
            },
        )


@register_checker("checks.aws.ecr_images:EcrImagesChecker")
def _factory(ctx: RunContext, bootstrap: Bootstrap) -> EcrImagesChecker:
    account_id = str(bootstrap.get("aws_account_id") or "")
    if not account_id:
        raise RuntimeError("aws_account_id missing from bootstrap (required for EcrImagesChecker)")
    billing_account_id = str(bootstrap.get("aws_billing_account_id") or account_id)
    return EcrImagesChecker(
        account=AwsAccountContext(account_id=account_id, billing_account_id=billing_account_id),
    )
