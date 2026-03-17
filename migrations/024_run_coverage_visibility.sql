-- Coverage visibility read model for run-scoped scan health.

CREATE TABLE IF NOT EXISTS run_checker_coverage (
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  run_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT '',
  service TEXT NOT NULL,
  checker_id TEXT NOT NULL,
  checker_scope TEXT NOT NULL,
  status TEXT NOT NULL,
  findings_count BIGINT NOT NULL DEFAULT 0,
  duration_ms BIGINT NULL,
  confidence TEXT NOT NULL DEFAULT 'none',
  completeness_pct NUMERIC(5,2) NULL,
  permission_gap_count BIGINT NOT NULL DEFAULT 0,
  error_class TEXT NULL,
  error_code TEXT NULL,
  error_message TEXT NULL,
  skip_reason TEXT NULL,
  started_at TIMESTAMPTZ NULL,
  finished_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, workspace, run_id, account_id, region, service, checker_id),
  CONSTRAINT ck_run_checker_coverage_status CHECK (
    status IN (
      'not_assessed',
      'assessed_no_issue',
      'assessed_with_findings',
      'assessment_failed',
      'skipped'
    )
  ),
  CONSTRAINT ck_run_checker_coverage_scope CHECK (checker_scope IN ('global', 'regional')),
  CONSTRAINT ck_run_checker_coverage_confidence CHECK (confidence IN ('none', 'low', 'medium', 'high'))
);

CREATE INDEX IF NOT EXISTS idx_run_checker_coverage_tenant_ws_run
  ON run_checker_coverage (tenant_id, workspace, run_id);

CREATE INDEX IF NOT EXISTS idx_run_checker_coverage_tenant_ws_run_status
  ON run_checker_coverage (tenant_id, workspace, run_id, status);

CREATE INDEX IF NOT EXISTS idx_run_checker_coverage_tenant_ws_run_service_region
  ON run_checker_coverage (tenant_id, workspace, run_id, service, region);

CREATE INDEX IF NOT EXISTS idx_run_checker_coverage_tenant_ws_run_checker
  ON run_checker_coverage (tenant_id, workspace, run_id, checker_id);

CREATE TABLE IF NOT EXISTS run_coverage_issues (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  run_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT '',
  service TEXT NOT NULL,
  checker_id TEXT NOT NULL,
  issue_type TEXT NOT NULL,
  operation TEXT NULL,
  error_code TEXT NULL,
  message TEXT NULL,
  is_retryable BOOLEAN NOT NULL DEFAULT FALSE,
  severity TEXT NOT NULL DEFAULT 'info',
  payload JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_run_coverage_issues_type CHECK (
    issue_type IN (
      'missing_permission',
      'throttled',
      'api_error',
      'malformed_source_data',
      'unsupported_scope',
      'internal_checker_error'
    )
  )
);

CREATE INDEX IF NOT EXISTS idx_run_coverage_issues_tenant_ws_run
  ON run_coverage_issues (tenant_id, workspace, run_id);

CREATE INDEX IF NOT EXISTS idx_run_coverage_issues_tenant_ws_run_type
  ON run_coverage_issues (tenant_id, workspace, run_id, issue_type);

CREATE INDEX IF NOT EXISTS idx_run_coverage_issues_tenant_ws_run_service_region
  ON run_coverage_issues (tenant_id, workspace, run_id, service, region);

CREATE TABLE IF NOT EXISTS run_coverage_summary (
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  run_id TEXT NOT NULL,
  targets_total BIGINT NOT NULL,
  assessed_total BIGINT NOT NULL,
  assessed_with_findings BIGINT NOT NULL,
  assessed_no_issue BIGINT NOT NULL,
  assessment_failed BIGINT NOT NULL,
  skipped_total BIGINT NOT NULL,
  not_assessed_total BIGINT NOT NULL,
  permission_gap_count BIGINT NOT NULL,
  coverage_pct NUMERIC(5,2) NOT NULL,
  coverage_status TEXT NOT NULL,
  confidence TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, workspace, run_id),
  CONSTRAINT ck_run_coverage_summary_status CHECK (coverage_status IN ('healthy', 'partial', 'degraded', 'failed')),
  CONSTRAINT ck_run_coverage_summary_confidence CHECK (confidence IN ('none', 'low', 'medium', 'high'))
);

ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS coverage_pct NUMERIC(5,2) NULL;

ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS coverage_status TEXT NULL;

ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS coverage_targets BIGINT NULL;

ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS coverage_failed BIGINT NULL;

ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS permission_gap_count BIGINT NULL;
