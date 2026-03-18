-- rule_id: aws.s3.correlation.replication.retention.drift
-- name: S3 replication and retention drift
-- enabled: true
-- required_check_ids: aws.s3.cost.replication.review, aws.s3.governance.lifecycle.missing, aws.s3.cost.lifecycle.transition.review, aws.s3.cost.intelligent_tiering.review

-- Correlates buckets where replication complexity coexists with weak lifecycle
-- or tiering controls. This surfaces a more specific storage-governance issue
-- than generic bucket control stacking: replicated data classes may be drifting
-- away from intended retention and storage-class policy.

WITH bucket_signals AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    scope.resource_id AS bucket_name,
    scope.resource_arn AS bucket_arn,

    MAX(CASE WHEN check_id = 'aws.s3.cost.replication.review' THEN 1 ELSE 0 END) AS sig_replication_review,
    MAX(CASE WHEN check_id = 'aws.s3.governance.lifecycle.missing' THEN 1 ELSE 0 END) AS sig_lifecycle_missing,
    MAX(CASE WHEN check_id = 'aws.s3.cost.lifecycle.transition.review' THEN 1 ELSE 0 END) AS sig_transition_review,
    MAX(CASE WHEN check_id = 'aws.s3.cost.intelligent_tiering.review' THEN 1 ELSE 0 END) AS sig_intelligent_tiering_review,

    MAX(COALESCE(dimensions['replication_pattern'], '')) AS replication_pattern,
    MAX(COALESCE(dimensions['recommendation_focus'], '')) AS replication_focus,
    MAX(COALESCE(dimensions['recommended_transition_target'], '')) AS recommended_transition_target,

    LIST(DISTINCT fingerprint) AS source_fingerprints
  FROM rule_input
  WHERE status = 'fail'
    AND scope.resource_type = 'bucket'
    AND check_id IN (
      'aws.s3.cost.replication.review',
      'aws.s3.governance.lifecycle.missing',
      'aws.s3.cost.lifecycle.transition.review',
      'aws.s3.cost.intelligent_tiering.review'
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

  'aws.s3.correlation.replication.retention.drift' AS check_id,
  'S3 replication and retention drift' AS check_name,
  'governance' AS category,
  'storage' AS sub_category,
  ['FinOps','Operations','Resilience'] AS frameworks,

  'fail' AS status,

  CASE
    WHEN b.replication_pattern = 'cold_data_replication_review'
      AND (b.sig_lifecycle_missing = 1 OR b.sig_transition_review = 1)
      THEN struct_pack(level:='high', score:=880)
    ELSE struct_pack(level:='medium', score:=760)
  END AS severity,

  0 AS priority,
  'S3 bucket replication setup appears misaligned with retention or storage-class controls' AS title,
  (
    'Bucket "' || b.bucket_name || '" shows replication complexity alongside missing or incomplete retention/storage optimization controls. '
    || CASE WHEN b.sig_lifecycle_missing = 1 THEN '[lifecycle_missing] ' ELSE '' END
    || CASE WHEN b.sig_transition_review = 1 THEN '[transition_review] ' ELSE '' END
    || CASE WHEN b.sig_intelligent_tiering_review = 1 THEN '[intelligent_tiering_review] ' ELSE '' END
    || CASE WHEN b.replication_pattern <> '' THEN ('Replication pattern=' || b.replication_pattern || '. ') ELSE '' END
    || CASE WHEN b.recommended_transition_target <> '' THEN ('Suggested transition target=' || b.recommended_transition_target || '. ') ELSE '' END
    || 'Review replication scope, destination storage classes, and lifecycle/tiering policy together so replicated data does not drift into unnecessary long-term cost or retention exposure.'
  ) AS message,
  'Review bucket replication scope and destination classes together with lifecycle transitions and intelligent-tiering policy. Align retention behavior before replicated colder data accumulates in the wrong class or for too long.' AS recommendation,

  '' AS remediation,
  [] AS links,

  struct_pack(
    monthly_savings := NULL,
    monthly_cost := NULL,
    one_time_savings := NULL,
    confidence := 40,
    notes := 'Correlated storage-governance signal. This rule intentionally does not claim direct savings ownership.'
  ) AS estimated,

  NULL AS actual,
  NULL AS lifecycle,
  map([],[]) AS tags,
  map([],[]) AS labels,
  map(
    ['bucket_name','signals','replication_pattern','replication_focus','recommended_transition_target'],
    [
      b.bucket_name,
      TRIM(BOTH ',' FROM
        (CASE WHEN b.sig_lifecycle_missing = 1 THEN 'lifecycle_missing,' ELSE '' END) ||
        (CASE WHEN b.sig_transition_review = 1 THEN 'transition_review,' ELSE '' END) ||
        (CASE WHEN b.sig_intelligent_tiering_review = 1 THEN 'intelligent_tiering_review,' ELSE '' END)
      ),
      COALESCE(b.replication_pattern,''),
      COALESCE(b.replication_focus,''),
      COALESCE(b.recommended_transition_target,'')
    ]
  ) AS dimensions,
  map([],[]) AS metrics,
  ('{"correlation_rule":"s3_replication_retention_drift"}') AS metadata_json,

  b.source_fingerprints AS source_fingerprints

FROM bucket_signals b
WHERE b.sig_replication_review = 1
  AND (
    b.sig_lifecycle_missing = 1
    OR b.sig_transition_review = 1
    OR b.sig_intelligent_tiering_review = 1
  );
