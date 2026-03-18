-- rule_id: aws.s3.correlation.bucket.control.conflict
-- name: S3 bucket control conflict
-- enabled: true
-- required_check_ids: aws.s3.governance.public.access.block.missing, aws.s3.governance.lifecycle.missing, aws.s3.cost.replication.review

-- Correlates stacked S3 control gaps on the same bucket:
--   - missing public access block
--   - missing lifecycle policy
--   - replication review
--
-- This is meant as a governance/intelligence signal rather than a pure savings
-- recommendation. When a bucket is exposed and also lacks lifecycle or has
-- replication complexity, it deserves a higher-confidence operator review.

WITH
bucket_signals AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    scope.resource_id AS bucket_name,
    scope.resource_arn AS bucket_arn,

    MAX(CASE WHEN check_id = 'aws.s3.governance.public.access.block.missing' THEN 1 ELSE 0 END) AS sig_public_access_block_missing,
    MAX(CASE WHEN check_id = 'aws.s3.governance.lifecycle.missing' THEN 1 ELSE 0 END) AS sig_lifecycle_missing,
    MAX(CASE WHEN check_id = 'aws.s3.cost.replication.review' THEN 1 ELSE 0 END) AS sig_replication_review,

    MAX(COALESCE(dimensions['replication_pattern'], '')) AS replication_pattern,
    MAX(COALESCE(dimensions['recommendation_focus'], '')) AS replication_focus,

    LIST(DISTINCT fingerprint) AS source_fingerprints
  FROM rule_input
  WHERE status = 'fail'
    AND scope.resource_type = 'bucket'
    AND check_id IN (
      'aws.s3.governance.public.access.block.missing',
      'aws.s3.governance.lifecycle.missing',
      'aws.s3.cost.replication.review'
    )
  GROUP BY ALL
)

SELECT
  b.tenant_id,
  b.workspace_id,
  b.run_id,
  (SELECT MAX(run_ts) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id) AS run_ts,
  (SELECT ANY_VALUE(engine_name) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id) AS engine_name,
  (SELECT ANY_VALUE(engine_version) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id) AS engine_version,
  (SELECT ANY_VALUE(rulepack_version) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id) AS rulepack_version,

  struct_pack(
    cloud := (SELECT ANY_VALUE(scope.cloud) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id LIMIT 1),
    provider_partition := (SELECT ANY_VALUE(scope.provider_partition) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id LIMIT 1),
    billing_account_id := (SELECT ANY_VALUE(scope.billing_account_id) FROM rule_input WHERE tenant_id=b.tenant_id AND workspace_id=b.workspace_id AND run_id=b.run_id LIMIT 1),
    account_id := b.account_id,
    region := b.region,
    service := 'AmazonS3',
    resource_type := 'bucket',
    resource_id := b.bucket_name,
    resource_arn := b.bucket_arn
  ) AS scope,

  'aws.s3.correlation.bucket.control.conflict' AS check_id,
  'S3 bucket control conflict' AS check_name,
  'governance' AS category,
  'storage' AS sub_category,
  ['FinOps','Security','Operations'] AS frameworks,

  'fail' AS status,

  CASE
    WHEN b.sig_public_access_block_missing = 1 AND b.sig_lifecycle_missing = 1 AND b.sig_replication_review = 1
      THEN struct_pack(level:='high', score:=890)
    ELSE struct_pack(level:='medium', score:=770)
  END AS severity,

  0 AS priority,
  'S3 bucket has stacked exposure, lifecycle, and replication control gaps' AS title,
  (
    'Bucket "' || b.bucket_name || '" has multiple overlapping control gaps. '
    || CASE WHEN b.sig_public_access_block_missing = 1 THEN '[public_access_block_missing] ' ELSE '' END
    || CASE WHEN b.sig_lifecycle_missing = 1 THEN '[lifecycle_missing] ' ELSE '' END
    || CASE WHEN b.sig_replication_review = 1 THEN '[replication_review] ' ELSE '' END
    || CASE WHEN b.replication_pattern <> '' THEN ('Replication pattern=' || b.replication_pattern || '. ') ELSE '' END
    || 'Review exposure controls, retention/lifecycle policy, and replication scope together to avoid conflicting bucket behavior.'
  ) AS message,
  'Enable strong public-access guardrails, define explicit lifecycle retention, and validate whether current replication scope matches the intended data-class and compliance pattern.' AS recommendation,

  '' AS remediation,
  [] AS links,

  struct_pack(
    monthly_savings := NULL,
    monthly_cost := NULL,
    one_time_savings := NULL,
    confidence := 35,
    notes := 'Correlated governance signal. This rule intentionally does not claim savings ownership.'
  ) AS estimated,

  NULL AS actual,
  NULL AS lifecycle,
  map([],[]) AS tags,
  map([],[]) AS labels,
  map(
    ['bucket_name','signals','replication_pattern','replication_focus'],
    [
      b.bucket_name,
      TRIM(BOTH ',' FROM
        (CASE WHEN b.sig_public_access_block_missing = 1 THEN 'public_access_block_missing,' ELSE '' END) ||
        (CASE WHEN b.sig_lifecycle_missing = 1 THEN 'lifecycle_missing,' ELSE '' END) ||
        (CASE WHEN b.sig_replication_review = 1 THEN 'replication_review,' ELSE '' END)
      ),
      COALESCE(b.replication_pattern,''),
      COALESCE(b.replication_focus,'')
    ]
  ) AS dimensions,
  map([],[]) AS metrics,
  ('{"correlation_rule":"s3_bucket_control_conflict"}') AS metadata_json,

  b.source_fingerprints AS source_fingerprints

FROM bucket_signals b
WHERE (
    b.sig_public_access_block_missing
  + b.sig_lifecycle_missing
  + b.sig_replication_review
 ) >= 2
