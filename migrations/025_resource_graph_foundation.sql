-- Resource relationship graph foundation tables.

CREATE TABLE IF NOT EXISTS resource_graph_nodes_run (
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  run_id TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  provider TEXT NOT NULL,
  service TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  account_id TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT '',
  resource_id TEXT NULL,
  resource_arn TEXT NULL,
  resource_name TEXT NULL,
  parent_resource_key TEXT NULL,
  state TEXT NULL,
  tags_json JSONB NULL,
  attributes_json JSONB NULL,
  owner_hint TEXT NULL,
  is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
  first_seen_in_run TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, workspace, run_id, resource_key)
);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_run_tenant_ws_run
  ON resource_graph_nodes_run (tenant_id, workspace, run_id);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_run_tenant_ws_run_service_type
  ON resource_graph_nodes_run (tenant_id, workspace, run_id, service, resource_type);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_run_tenant_ws_run_account_region
  ON resource_graph_nodes_run (tenant_id, workspace, run_id, account_id, region);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_run_tenant_ws_run_parent
  ON resource_graph_nodes_run (tenant_id, workspace, run_id, parent_resource_key);

CREATE TABLE IF NOT EXISTS resource_graph_edges_run (
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  run_id TEXT NOT NULL,
  edge_key TEXT NOT NULL,
  from_resource_key TEXT NOT NULL,
  to_resource_key TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  service TEXT NOT NULL,
  account_id TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT '',
  directionality TEXT NOT NULL DEFAULT 'directed',
  confidence TEXT NOT NULL DEFAULT 'high',
  source_kind TEXT NOT NULL,
  attributes_json JSONB NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, workspace, run_id, edge_key),
  CONSTRAINT ck_resource_graph_edges_run_directionality CHECK (directionality IN ('directed', 'undirected')),
  CONSTRAINT ck_resource_graph_edges_run_confidence CHECK (confidence IN ('none', 'low', 'medium', 'high')),
  CONSTRAINT ck_resource_graph_edges_run_source_kind CHECK (source_kind IN ('api_direct', 'derived', 'inferred'))
);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_run_tenant_ws_run_from
  ON resource_graph_edges_run (tenant_id, workspace, run_id, from_resource_key);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_run_tenant_ws_run_to
  ON resource_graph_edges_run (tenant_id, workspace, run_id, to_resource_key);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_run_tenant_ws_run_type
  ON resource_graph_edges_run (tenant_id, workspace, run_id, edge_type);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_run_tenant_ws_run_service_scope
  ON resource_graph_edges_run (tenant_id, workspace, run_id, service, account_id, region);

CREATE TABLE IF NOT EXISTS resource_graph_nodes_current (
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  provider TEXT NOT NULL,
  service TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  account_id TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT '',
  resource_id TEXT NULL,
  resource_arn TEXT NULL,
  resource_name TEXT NULL,
  parent_resource_key TEXT NULL,
  state TEXT NULL,
  tags_json JSONB NULL,
  attributes_json JSONB NULL,
  owner_hint TEXT NULL,
  is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
  latest_run_id TEXT NOT NULL,
  latest_run_ts TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, workspace, resource_key)
);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_current_tenant_ws_service_type
  ON resource_graph_nodes_current (tenant_id, workspace, service, resource_type);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_current_tenant_ws_account_region
  ON resource_graph_nodes_current (tenant_id, workspace, account_id, region);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_current_tenant_ws_parent
  ON resource_graph_nodes_current (tenant_id, workspace, parent_resource_key);

CREATE INDEX IF NOT EXISTS idx_resource_graph_nodes_current_tenant_ws_latest_run
  ON resource_graph_nodes_current (tenant_id, workspace, latest_run_id);

CREATE TABLE IF NOT EXISTS resource_graph_edges_current (
  tenant_id TEXT NOT NULL,
  workspace TEXT NOT NULL,
  edge_key TEXT NOT NULL,
  from_resource_key TEXT NOT NULL,
  to_resource_key TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  service TEXT NOT NULL,
  account_id TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT '',
  directionality TEXT NOT NULL DEFAULT 'directed',
  confidence TEXT NOT NULL DEFAULT 'high',
  source_kind TEXT NOT NULL,
  attributes_json JSONB NULL,
  latest_run_id TEXT NOT NULL,
  latest_run_ts TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, workspace, edge_key),
  CONSTRAINT ck_resource_graph_edges_current_directionality CHECK (directionality IN ('directed', 'undirected')),
  CONSTRAINT ck_resource_graph_edges_current_confidence CHECK (confidence IN ('none', 'low', 'medium', 'high')),
  CONSTRAINT ck_resource_graph_edges_current_source_kind CHECK (source_kind IN ('api_direct', 'derived', 'inferred'))
);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_current_tenant_ws_from
  ON resource_graph_edges_current (tenant_id, workspace, from_resource_key);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_current_tenant_ws_to
  ON resource_graph_edges_current (tenant_id, workspace, to_resource_key);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_current_tenant_ws_type
  ON resource_graph_edges_current (tenant_id, workspace, edge_type);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_current_tenant_ws_service_scope
  ON resource_graph_edges_current (tenant_id, workspace, service, account_id, region);

CREATE INDEX IF NOT EXISTS idx_resource_graph_edges_current_tenant_ws_latest_run
  ON resource_graph_edges_current (tenant_id, workspace, latest_run_id);
