-- rule_id: aws.elbv2.correlation.public.ingress.chain
-- name: Public ingress chain misconfiguration
-- enabled: true
-- required_check_ids: aws.elbv2.load.balancers.idle, aws.elbv2.load.balancers.no.registered.targets, aws.elbv2.load.balancers.no.healthy.targets

-- Correlates public ingress surfaces with backend/pathology signals:
--   - internet-facing ELB that is idle
--   - internet-facing ELB with no registered targets
--   - internet-facing ELB with no healthy targets
--
-- This emits when a public ingress point has at least two signals, which makes
-- it a stronger operator concern than either a pure cost issue or a pure health
-- issue alone.

WITH
lb_signals AS (
  SELECT
    tenant_id,
    workspace_id,
    run_id,
    scope.account_id AS account_id,
    scope.region AS region,
    scope.resource_id AS lb_name,
    scope.resource_arn AS lb_arn,

    MAX(CASE WHEN check_id = 'aws.elbv2.load.balancers.idle' THEN 1 ELSE 0 END) AS sig_idle,
    MAX(CASE WHEN check_id = 'aws.elbv2.load.balancers.no.registered.targets' THEN 1 ELSE 0 END) AS sig_no_targets,
    MAX(CASE WHEN check_id = 'aws.elbv2.load.balancers.no.healthy.targets' THEN 1 ELSE 0 END) AS sig_no_healthy_targets,

    MAX(COALESCE(dimensions['scheme'], '')) AS scheme,
    MAX(COALESCE(dimensions['lb_type'], '')) AS lb_type,
    MAX(COALESCE(dimensions['vpc_id'], '')) AS vpc_id,
    MAX(COALESCE(dimensions['subnet_ids'], '')) AS subnet_ids,
    MAX(COALESCE(dimensions['target_group_arns'], '')) AS target_group_arns,

    MAX(COALESCE(estimated.monthly_cost, 0)) AS monthly_cost,
    LIST(DISTINCT fingerprint) AS source_fingerprints
  FROM rule_input
  WHERE status = 'fail'
    AND check_id IN (
      'aws.elbv2.load.balancers.idle',
      'aws.elbv2.load.balancers.no.registered.targets',
      'aws.elbv2.load.balancers.no.healthy.targets'
    )
    AND scope.resource_type = 'load-balancer'
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
    service := 'ElasticLoadBalancingV2',
    resource_type := 'load-balancer',
    resource_id := s.lb_name,
    resource_arn := s.lb_arn
  ) AS scope,

  'aws.elbv2.correlation.public.ingress.chain' AS check_id,
  'Public ingress chain misconfiguration' AS check_name,
  'governance' AS category,
  'network' AS sub_category,
  ['FinOps','Security','Operations'] AS frameworks,

  'fail' AS status,

  CASE
    WHEN s.sig_no_targets = 1 THEN struct_pack(level:='high', score:=880)
    ELSE struct_pack(level:='high', score:=840)
  END AS severity,

  0 AS priority,
  'Internet-facing load balancer has a broken or low-value backend chain' AS title,
  (
    'Load balancer "' || s.lb_name || '" is internet-facing and shows multiple ingress-chain warning signals. '
    || CASE WHEN s.sig_idle = 1 THEN '[idle] ' ELSE '' END
    || CASE WHEN s.sig_no_targets = 1 THEN '[no_registered_targets] ' ELSE '' END
    || CASE WHEN s.sig_no_healthy_targets = 1 THEN '[no_healthy_targets] ' ELSE '' END
    || CASE WHEN s.target_group_arns <> '' THEN ('Target groups=' || s.target_group_arns || '. ') ELSE '' END
    || 'Review whether this public ingress should still exist and whether its target chain is intentionally exposed.'
  ) AS message,
  'If this ingress is no longer needed, decommission it. Otherwise validate target registration, health checks, and exposure intent before leaving an internet-facing surface in place.' AS recommendation,

  '' AS remediation,
  [] AS links,

  struct_pack(
    monthly_savings := CASE WHEN s.monthly_cost > 0 THEN s.monthly_cost ELSE NULL END,
    monthly_cost := CASE WHEN s.monthly_cost > 0 THEN s.monthly_cost ELSE NULL END,
    one_time_savings := NULL,
    confidence := 45,
    notes := 'Cost uses the strongest available ELB monthly estimate when present; this correlation is primarily a governance and dependency-chain signal.'
  ) AS estimated,

  NULL AS actual,
  NULL AS lifecycle,
  map([],[]) AS tags,
  map([],[]) AS labels,
  map(
    ['lb_name','scheme','lb_type','signals','vpc_id','subnet_ids','target_group_arns'],
    [
      s.lb_name,
      COALESCE(s.scheme,''),
      COALESCE(s.lb_type,''),
      TRIM(BOTH ',' FROM
        (CASE WHEN s.sig_idle = 1 THEN 'idle,' ELSE '' END) ||
        (CASE WHEN s.sig_no_targets = 1 THEN 'no_registered_targets,' ELSE '' END) ||
        (CASE WHEN s.sig_no_healthy_targets = 1 THEN 'no_healthy_targets,' ELSE '' END)
      ),
      COALESCE(s.vpc_id,''),
      COALESCE(s.subnet_ids,''),
      COALESCE(s.target_group_arns,'')
    ]
  ) AS dimensions,
  map([],[]) AS metrics,
  ('{"correlation_rule":"public_ingress_chain"}') AS metadata_json,

  s.source_fingerprints AS source_fingerprints

FROM lb_signals s
WHERE LOWER(COALESCE(s.scheme, '')) = 'internet-facing'
  AND (
      s.sig_idle
    + s.sig_no_targets
    + s.sig_no_healthy_targets
  ) >= 2
