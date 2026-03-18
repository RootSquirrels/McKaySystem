-- rule_id: aws.rds.correlation.snapshots.orphaned.lineage
-- name: RDS orphaned snapshot lineage
-- enabled: true
-- required_check_ids: aws.rds.snapshots.orphaned

-- Correlates multiple orphaned RDS snapshots that point to the same missing
-- source DB instance or DB cluster. A single orphaned snapshot can be normal
-- during transitions; repeated orphaned snapshots from the same missing source
-- are a stronger orphaned-infra lineage signal.

WITH orphaned_snapshots AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    COALESCE(dimensions['source_identifier'], '') AS source_identifier,
    COALESCE(dimensions['source_kind'], '') AS source_kind,
    COUNT(*) AS snapshot_count,
    SUM(COALESCE(estimated.monthly_cost, 0)) AS monthly_cost,
    LIST(DISTINCT scope.resource_id) AS snapshot_ids,
    LIST(DISTINCT fingerprint) AS source_fingerprints
  FROM rule_input
  WHERE status = 'fail'
    AND check_id = 'aws.rds.snapshots.orphaned'
    AND COALESCE(dimensions['source_identifier'], '') <> ''
  GROUP BY ALL
)

SELECT
  s.tenant_id,
  s.workspace_id,
  s.run_id,
  (SELECT MAX(run_ts) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id) AS run_ts,
  (SELECT ANY_VALUE(engine_name) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id) AS engine_name,
  (SELECT ANY_VALUE(engine_version) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id) AS engine_version,
  (SELECT ANY_VALUE(rulepack_version) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id) AS rulepack_version,

  struct_pack(
    cloud := (SELECT ANY_VALUE(scope.cloud) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id LIMIT 1),
    provider_partition := (SELECT ANY_VALUE(scope.provider_partition) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id LIMIT 1),
    billing_account_id := (SELECT ANY_VALUE(scope.billing_account_id) FROM rule_input WHERE tenant_id=s.tenant_id AND workspace_id=s.workspace_id AND run_id=s.run_id LIMIT 1),
    account_id := s.account_id,
    region := s.region,
    service := 'AmazonRDS',
    resource_type := s.source_kind,
    resource_id := s.source_identifier,
    resource_arn := ''
  ) AS scope,

  'aws.rds.correlation.snapshots.orphaned.lineage' AS check_id,
  'RDS orphaned snapshot lineage' AS check_name,
  'waste' AS category,
  'backup' AS sub_category,
  ['FinOps','Operations','Resilience'] AS frameworks,

  'fail' AS status,

  CASE
    WHEN s.snapshot_count >= 4 THEN struct_pack(level:='high', score:=880)
    WHEN s.snapshot_count >= 2 THEN struct_pack(level:='medium', score:=760)
    ELSE struct_pack(level:='low', score:=620)
  END AS severity,

  0 AS priority,
  'Multiple orphaned RDS snapshots belong to the same missing source lineage' AS title,
  (
    'Source ' || s.source_kind || ' "' || s.source_identifier || '" has '
    || CAST(s.snapshot_count AS VARCHAR) || ' orphaned snapshot(s) in region ' || s.region || '. '
    || 'This suggests a stale snapshot lineage rather than a one-off leftover backup. '
    || 'Review whether the missing source was intentionally retired and prune retained snapshots that no longer serve restore requirements.'
  ) AS message,
  'Validate restore and retention requirements for the missing source, then delete or archive the orphaned snapshot lineage if it is no longer needed.' AS recommendation,

  '' AS remediation,
  [] AS links,

  struct_pack(
    monthly_savings := CASE WHEN s.monthly_cost > 0 THEN s.monthly_cost ELSE NULL END,
    monthly_cost := CASE WHEN s.monthly_cost > 0 THEN s.monthly_cost ELSE NULL END,
    one_time_savings := NULL,
    confidence := 55,
    notes := 'Monthly estimate aggregates orphaned snapshot storage estimates from the same missing RDS source lineage.'
  ) AS estimated,

  NULL AS actual,
  NULL AS lifecycle,
  map([],[]) AS tags,
  map([],[]) AS labels,
  map(
    ['source_identifier','source_kind','snapshot_count','snapshot_ids'],
    [
      s.source_identifier,
      s.source_kind,
      CAST(s.snapshot_count AS VARCHAR),
      ARRAY_TO_STRING(s.snapshot_ids, ',')
    ]
  ) AS dimensions,
  map([],[]) AS metrics,
  ('{"correlation_rule":"rds_orphaned_snapshot_lineage","snapshot_count":' || CAST(s.snapshot_count AS VARCHAR) || '}') AS metadata_json,

  s.source_fingerprints AS source_fingerprints

FROM orphaned_snapshots s
WHERE s.snapshot_count >= 2;
