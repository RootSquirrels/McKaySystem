-- rule_id: aws.ec2.correlation.ebs.orphaned.lineage
-- name: EBS orphaned storage lineage
-- enabled: true
-- required_check_ids: aws.ec2.ebs.unattached.volume, aws.ec2.ebs.old.snapshot

-- Correlates orphaned storage lineage:
--   - aws.ec2.ebs.unattached.volume
--   - aws.ec2.ebs.old.snapshot
--
-- Emits a meta-finding per volume when an unattached volume also has at least
-- one old snapshot for the same backing volume id. This is a stronger cleanup
-- signal than either leaf finding alone because it suggests leftover lineage
-- rather than an isolated artifact.

WITH
unattached_volumes AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    scope.resource_id AS volume_id,
    scope.resource_arn AS volume_arn,
    MAX(COALESCE(estimated.monthly_cost, 0)) AS volume_monthly_cost,
    MAX(COALESCE(dimensions['volume_type'], '')) AS volume_type,
    MAX(COALESCE(dimensions['size_gb'], '')) AS size_gb,
    MAX(COALESCE(dimensions['age_days'], '')) AS age_days,
    LIST(DISTINCT fingerprint) AS volume_source_fps
  FROM rule_input
  WHERE status = 'fail'
    AND check_id = 'aws.ec2.ebs.unattached.volume'
    AND scope.resource_type = 'ebs_volume'
  GROUP BY ALL
),

old_snapshots AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    COALESCE(dimensions['volume_id'], '') AS volume_id,
    COUNT(*) AS old_snapshot_count,
    SUM(COALESCE(estimated.monthly_cost, 0)) AS snapshot_monthly_cost,
    MAX(COALESCE(dimensions['age_days'], '')) AS max_snapshot_age_days,
    LIST(DISTINCT fingerprint) AS snapshot_source_fps
  FROM rule_input
  WHERE status = 'fail'
    AND check_id = 'aws.ec2.ebs.old.snapshot'
    AND scope.resource_type = 'ebs_snapshot'
    AND COALESCE(dimensions['volume_id'], '') <> ''
  GROUP BY ALL
),

combined AS (
  SELECT
    v.tenant_id,
    v.workspace_id,
    v.run_id,
    v.account_id,
    v.region,
    v.volume_id,
    v.volume_arn,
    v.volume_monthly_cost,
    COALESCE(s.snapshot_monthly_cost, 0) AS snapshot_monthly_cost,
    COALESCE(s.old_snapshot_count, 0) AS old_snapshot_count,
    v.volume_type,
    v.size_gb,
    v.age_days,
    COALESCE(s.max_snapshot_age_days, '') AS max_snapshot_age_days,
    LIST_CONCAT(v.volume_source_fps, COALESCE(s.snapshot_source_fps, [])) AS source_fingerprints
  FROM unattached_volumes v
  INNER JOIN old_snapshots s
    ON v.tenant_id = s.tenant_id
   AND v.workspace_id = s.workspace_id
   AND v.run_id = s.run_id
   AND v.account_id = s.account_id
   AND v.region = s.region
   AND v.volume_id = s.volume_id
)

SELECT
  c.tenant_id,
  c.workspace_id,
  c.run_id,
  (SELECT MAX(run_ts) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id) AS run_ts,
  (SELECT ANY_VALUE(engine_name) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id) AS engine_name,
  (SELECT ANY_VALUE(engine_version) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id) AS engine_version,
  (SELECT ANY_VALUE(rulepack_version) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id) AS rulepack_version,

  struct_pack(
    cloud := (SELECT ANY_VALUE(scope.cloud) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id LIMIT 1),
    provider_partition := (SELECT ANY_VALUE(scope.provider_partition) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id LIMIT 1),
    billing_account_id := (SELECT ANY_VALUE(scope.billing_account_id) FROM rule_input WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND run_id=c.run_id LIMIT 1),
    account_id := c.account_id,
    region := c.region,
    service := 'AmazonEC2',
    resource_type := 'ebs_volume',
    resource_id := c.volume_id,
    resource_arn := c.volume_arn
  ) AS scope,

  'aws.ec2.correlation.ebs.orphaned.lineage' AS check_id,
  'EBS orphaned storage lineage' AS check_name,
  'governance' AS category,
  'storage' AS sub_category,
  ['FinOps','Operations'] AS frameworks,

  'fail' AS status,

  CASE
    WHEN (c.volume_monthly_cost + c.snapshot_monthly_cost) >= 50 THEN struct_pack(level:='high', score:=880)
    ELSE struct_pack(level:='medium', score:=760)
  END AS severity,

  0 AS priority,
  'Unattached EBS volume has leftover snapshot lineage' AS title,
  (
    'Volume ' || c.volume_id || ' is unattached and still has '
    || CAST(c.old_snapshot_count AS VARCHAR) || ' old snapshot(s) referencing the same lineage. '
    || CASE WHEN c.size_gb <> '' THEN ('Volume size=' || c.size_gb || 'GB. ') ELSE '' END
    || CASE WHEN c.volume_type <> '' THEN ('Type=' || c.volume_type || '. ') ELSE '' END
    || CASE WHEN c.age_days <> '' THEN ('Volume idle ~' || c.age_days || ' days. ') ELSE '' END
    || CASE WHEN c.max_snapshot_age_days <> '' THEN ('Oldest correlated snapshot age ~' || c.max_snapshot_age_days || ' days. ') ELSE '' END
    || 'Review whether the volume and its retained snapshots are both still needed before cleanup.'
  ) AS message,
  'Validate restore requirements, then delete or archive the unattached volume and prune stale snapshots that are no longer required.' AS recommendation,

  '' AS remediation,
  [] AS links,

  struct_pack(
    monthly_savings := CASE
      WHEN (c.volume_monthly_cost + c.snapshot_monthly_cost) > 0
      THEN (c.volume_monthly_cost + c.snapshot_monthly_cost)
      ELSE NULL
    END,
    monthly_cost := CASE
      WHEN (c.volume_monthly_cost + c.snapshot_monthly_cost) > 0
      THEN (c.volume_monthly_cost + c.snapshot_monthly_cost)
      ELSE NULL
    END,
    one_time_savings := NULL,
    confidence := 50,
    notes := 'Monthly cost is the sum of unattached volume cost and old-snapshot storage estimates, which should be treated as a review-level upper bound.'
  ) AS estimated,

  NULL AS actual,
  NULL AS lifecycle,
  map([],[]) AS tags,
  map([],[]) AS labels,
  map(
    ['volume_id','old_snapshot_count','volume_type','size_gb','volume_age_days','max_snapshot_age_days'],
    [c.volume_id, CAST(c.old_snapshot_count AS VARCHAR), COALESCE(c.volume_type,''), COALESCE(c.size_gb,''), COALESCE(c.age_days,''), COALESCE(c.max_snapshot_age_days,'')]
  ) AS dimensions,
  map([],[]) AS metrics,
  ('{"correlation_rule":"ebs_orphaned_lineage","old_snapshot_count":' || CAST(c.old_snapshot_count AS VARCHAR) || '}') AS metadata_json,

  c.source_fingerprints AS source_fingerprints

FROM combined c
WHERE c.old_snapshot_count >= 1
