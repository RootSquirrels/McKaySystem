-- Migration 026: Tenant administration foundation
-- Adds explicit tenant workspace registry and future-workspace role bindings.

CREATE TABLE IF NOT EXISTS tenant_workspaces (
    tenant_id TEXT NOT NULL,
    workspace TEXT NOT NULL,
    display_name TEXT NULL,
    provider TEXT NOT NULL DEFAULT 'unknown',
    scope_kind TEXT NOT NULL DEFAULT 'unknown',
    scope_native_id TEXT NULL,
    environment TEXT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NULL,
    updated_by TEXT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at TIMESTAMPTZ NULL,
    archived_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace),
    CONSTRAINT chk_tenant_workspaces_status
      CHECK (status IN ('active', 'suspended', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_tenant_workspaces_tenant_status
  ON tenant_workspaces (tenant_id, status, workspace);

CREATE INDEX IF NOT EXISTS idx_tenant_workspaces_tenant_provider
  ON tenant_workspaces (tenant_id, provider, workspace);

INSERT INTO tenant_workspaces (
    tenant_id,
    workspace,
    display_name,
    provider,
    scope_kind,
    status,
    registered_at,
    activated_at,
    updated_at
)
SELECT
    src.tenant_id,
    src.workspace,
    src.workspace,
    'unknown',
    'unknown',
    'active',
    now(),
    now(),
    now()
FROM (
    SELECT DISTINCT tenant_id, workspace
    FROM roles
    WHERE NOT (tenant_id = 'default' AND workspace = 'default')
) AS src
ON CONFLICT (tenant_id, workspace) DO NOTHING;

CREATE TABLE IF NOT EXISTS tenant_role_bindings (
    tenant_id TEXT NOT NULL,
    workspace TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    source_workspace TEXT NOT NULL,
    applies_to_future_workspaces BOOLEAN NOT NULL DEFAULT TRUE,
    granted_by TEXT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, workspace, user_id),
    CONSTRAINT chk_tenant_role_bindings_workspace
      CHECK (workspace = '__tenant__')
);

CREATE INDEX IF NOT EXISTS idx_tenant_role_bindings_tenant_role
  ON tenant_role_bindings (tenant_id, workspace, role_id);

INSERT INTO schema_migrations (version) VALUES ('026') ON CONFLICT (version) DO NOTHING;
