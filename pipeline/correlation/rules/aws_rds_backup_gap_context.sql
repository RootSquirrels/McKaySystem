-- rule_id: aws.rds.correlation.backup.gap.context
-- name: RDS backup gap with dependency context
-- enabled: true
-- required_check_ids: aws.rds.instances.stopped.storage, aws.rds.storage.overprovisioned, aws.rds.read.replica.unused, aws.backup.vaults.no.lifecycle, aws.backup.rules.no.lifecycle, aws.backup.plans.no.selections, aws.backup.recovery.points.stale

-- Correlates RDS instances with nearby backup-governance gaps in the same
-- account/region. This is intentionally dependency-context intelligence, not a
-- claim that the database is definitely unprotected. It tells operators that
-- storage-bearing database resources coexist with weak backup guardrails.

WITH
rds_signals AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    scope.resource_id AS db_instance_identifier,
    scope.resource_arn AS db_instance_arn,
    check_id,
    fingerprint,
    MAX(COALESCE(estimated.monthly_cost, 0)) AS db_monthly_cost,
    MAX(COALESCE(dimensions['db_cluster_identifier'], '')) AS db_cluster_identifier,
    MAX(COALESCE(dimensions['db_subnet_group'], '')) AS db_subnet_group
  FROM rule_input
  WHERE status = 'fail'
    AND check_id IN (
      'aws.rds.instances.stopped.storage',
      'aws.rds.storage.overprovisioned',
      'aws.rds.read.replica.unused'
    )
    AND scope.resource_type = 'db_instance'
  GROUP BY ALL
),

backup_signals AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,

    MAX(CASE WHEN check_id = 'aws.backup.vaults.no.lifecycle' THEN 1 ELSE 0 END) AS sig_vault_no_lifecycle,
    MAX(CASE WHEN check_id = 'aws.backup.rules.no.lifecycle' THEN 1 ELSE 0 END) AS sig_rule_no_lifecycle,
    MAX(CASE WHEN check_id = 'aws.backup.plans.no.selections' THEN 1 ELSE 0 END) AS sig_plan_no_selections,
    MAX(CASE WHEN check_id = 'aws.backup.recovery.points.stale' THEN 1 ELSE 0 END) AS sig_stale_recovery_points,

    COUNT(DISTINCT CASE WHEN check_id = 'aws.backup.vaults.no.lifecycle' THEN scope.resource_id END) AS vault_count,
    COUNT(DISTINCT CASE WHEN check_id = 'aws.backup.recovery.points.stale' THEN scope.resource_id END) AS stale_recovery_point_count,

    SUM(CASE WHEN check_id = 'aws.backup.recovery.points.stale' THEN COALESCE(estimated.monthly_cost, 0) ELSE 0 END) AS stale_monthly_cost,
    LIST(DISTINCT fingerprint) AS backup_source_fps
  FROM rule_input
  WHERE status = 'fail'
    AND check_id IN (
      'aws.backup.vaults.no.lifecycle',
      'aws.backup.rules.no.lifecycle',
      'aws.backup.plans.no.selections',
      'aws.backup.recovery.points.stale'
    )
  GROUP BY ALL
),

combined AS (
  SELECT
    r.tenant_id,
    r.workspace_id,
    r.run_id,
    r.account_id,
    r.region,
    r.db_instance_identifier,
    r.db_instance_arn,
    r.db_monthly_cost,
    r.db_cluster_identifier,
    r.db_subnet_group,
    b.sig_vault_no_lifecycle,
    b.sig_rule_no_lifecycle,
    b.sig_plan_no_selections,
    b.sig_stale_recovery_points,
    b.vault_count,
    b.stale_recovery_point_count,
    b.stale_monthly_cost,
    LIST_CONCAT([r.fingerprint], COALESCE(b.backup_source_fps, [])) AS source_fingerprints
  FROM rds_signals r
  INNER JOIN backup_signals b
    ON r.tenant_id = b.tenant_id
   AND r.workspace_id = b.workspace_id
   AND r.run_id = b.run_id
   AND r.account_id = b.account_id
   AND r.region = b.region
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
    service := 'AmazonRDS',
    resource_type := 'db_instance',
    resource_id := c.db_instance_identifier,
    resource_arn := c.db_instance_arn
  ) AS scope,

  'aws.rds.correlation.backup.gap.context' AS check_id,
  'RDS backup gap with dependency context' AS check_name,
  'governance' AS category,
  'backup' AS sub_category,
  ['FinOps','Resilience','Operations'] AS frameworks,

  'fail' AS status,

  CASE
    WHEN c.sig_plan_no_selections = 1 AND c.sig_vault_no_lifecycle = 1 THEN struct_pack(level:='high', score:=880)
    ELSE struct_pack(level:='medium', score:=760)
  END AS severity,

  0 AS priority,
  'RDS instance exists alongside backup-governance gaps in the same environment' AS title,
  (
    'RDS instance "' || c.db_instance_identifier || '" sits in an account/region with backup-governance gaps. '
    || CASE WHEN c.sig_vault_no_lifecycle = 1 THEN '[vault_no_lifecycle] ' ELSE '' END
    || CASE WHEN c.sig_rule_no_lifecycle = 1 THEN '[rule_no_lifecycle] ' ELSE '' END
    || CASE WHEN c.sig_plan_no_selections = 1 THEN '[plan_no_selections] ' ELSE '' END
    || CASE WHEN c.sig_stale_recovery_points = 1 THEN '[stale_recovery_points] ' ELSE '' END
    || CASE WHEN c.db_cluster_identifier <> '' THEN ('Cluster=' || c.db_cluster_identifier || '. ') ELSE '' END
    || CASE WHEN c.db_subnet_group <> '' THEN ('Subnet group=' || c.db_subnet_group || '. ') ELSE '' END
    || 'Validate whether this database is covered by the intended backup plan, vault retention, and recovery-point lifecycle.'
  ) AS message,
  'Review backup plan selections, rule lifecycle, and vault retention for the environment hosting this database. Confirm restore requirements before treating backup posture as acceptable.' AS recommendation,

  '' AS remediation,
  [] AS links,

  struct_pack(
    monthly_savings := NULL,
    monthly_cost := CASE WHEN c.stale_monthly_cost > 0 THEN c.stale_monthly_cost ELSE NULL END,
    one_time_savings := NULL,
    confidence := 35,
    notes := 'Dependency-context governance signal. Monthly cost is only the stale recovery-point estimate when available and is not attributed solely to this database.'
  ) AS estimated,

  NULL AS actual,
  NULL AS lifecycle,
  map([],[]) AS tags,
  map([],[]) AS labels,
  map(
    ['db_instance_identifier','db_cluster_identifier','db_subnet_group','signals','vault_count','stale_recovery_point_count'],
    [
      c.db_instance_identifier,
      COALESCE(c.db_cluster_identifier,''),
      COALESCE(c.db_subnet_group,''),
      TRIM(BOTH ',' FROM
        (CASE WHEN c.sig_vault_no_lifecycle = 1 THEN 'vault_no_lifecycle,' ELSE '' END) ||
        (CASE WHEN c.sig_rule_no_lifecycle = 1 THEN 'rule_no_lifecycle,' ELSE '' END) ||
        (CASE WHEN c.sig_plan_no_selections = 1 THEN 'plan_no_selections,' ELSE '' END) ||
        (CASE WHEN c.sig_stale_recovery_points = 1 THEN 'stale_recovery_points,' ELSE '' END)
      ),
      CAST(COALESCE(c.vault_count, 0) AS VARCHAR),
      CAST(COALESCE(c.stale_recovery_point_count, 0) AS VARCHAR)
    ]
  ) AS dimensions,
  map([],[]) AS metrics,
  ('{"correlation_rule":"rds_backup_gap_context"}') AS metadata_json,

  c.source_fingerprints AS source_fingerprints

FROM combined c
WHERE (
    c.sig_vault_no_lifecycle
  + c.sig_rule_no_lifecycle
  + c.sig_plan_no_selections
  + c.sig_stale_recovery_points
 ) >= 2
