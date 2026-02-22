# RBAC User and Role Guide

Status: Canonical  
Last reviewed: 2026-02-22

This guide shows how to:
- create a user with a password
- reset/update a user's password
- assign a role to a user
- apply role assignments per tenant/workspace scope

## Scope model (important)

RBAC is scoped by `tenant_id` + `workspace`.

Role assignment is workspace-scoped:
- assigning `editor` to `alice` in `acme/prod` does not assign it in `acme/dev`
- to grant the same role across multiple workspaces, repeat assignment per workspace

There is currently no tenant-wide role assignment endpoint.

## Default roles

System roles seeded from templates:
- `admin`: full permissions (includes `admin:full`)
- `editor`: broad read/write, but excludes sensitive admin actions
- `viewer`: read-only subset (`findings:read`, `runs:read`, `teams:read`, `sla:read`)

## Prerequisites

- Base URL:
  - legacy: `/api`
  - versioned: `/api/v1` (compat for the same handlers)
- For RBAC-protected endpoints, provide:
  - scope (`tenant_id`, `workspace`)
  - authentication (session token cookie or API key bearer)
- Recommended environment variables:

```bash
export BASE_URL="http://localhost:8000"
export TENANT_ID="acme"
export WORKSPACE="prod"
```

## 1) Login as an admin user

```bash
curl -sS -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"workspace\": \"$WORKSPACE\",
    \"email\": \"admin@acme.io\",
    \"password\": \"<admin-password>\"
  }" \
  -c /tmp/mck_cookies.txt
```

Notes:
- Save cookies (`-c`) and reuse them (`-b`) for subsequent calls.
- Use lowercase email to match login normalization behavior.

## 2) Create a user with password

Requires permission: `users:create`.

```bash
curl -sS -X POST "$BASE_URL/api/users" \
  -H "Content-Type: application/json" \
  -b /tmp/mck_cookies.txt \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"workspace\": \"$WORKSPACE\",
    \"user_id\": \"u_alice\",
    \"email\": \"alice@acme.io\",
    \"password\": \"<initial-password>\",
    \"full_name\": \"Alice Doe\",
    \"auth_provider\": \"local\",
    \"is_active\": true
  }"
```

## 3) Reset or update a user's password

Requires permission: `users:update`.

```bash
curl -sS -X PUT "$BASE_URL/api/users/u_alice" \
  -H "Content-Type: application/json" \
  -b /tmp/mck_cookies.txt \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"workspace\": \"$WORKSPACE\",
    \"password\": \"<new-password>\"
  }"
```

## 4) Assign a role to a user

Requires permission: `users:manage_roles`.

```bash
curl -sS -X PUT "$BASE_URL/api/users/u_alice/role" \
  -H "Content-Type: application/json" \
  -b /tmp/mck_cookies.txt \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"workspace\": \"$WORKSPACE\",
    \"role_id\": \"editor\",
    \"granted_by\": \"admin@acme.io\"
  }"
```

## 5) Verify role assignment

Requires permission: `users:manage_roles`.

```bash
curl -sS "$BASE_URL/api/users/u_alice/role?tenant_id=$TENANT_ID&workspace=$WORKSPACE" \
  -b /tmp/mck_cookies.txt
```

Expected shape:
- `role = null` when not assigned
- otherwise includes `role_id`, `permissions`, `granted_by`, `granted_at`

## 6) Validate with user login

```bash
curl -sS -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$TENANT_ID\",
    \"workspace\": \"$WORKSPACE\",
    \"email\": \"alice@acme.io\",
    \"password\": \"<new-password>\"
  }" \
  -c /tmp/alice_cookies.txt

curl -sS "$BASE_URL/api/auth/me?tenant_id=$TENANT_ID&workspace=$WORKSPACE" \
  -b /tmp/alice_cookies.txt
```

The `permissions` array reflects the assigned role in that scope.

## Applying roles across tenant/workspaces

Role assignment is per `(tenant_id, workspace)`.  
To apply `editor` for the same user to three workspaces, call role assignment three times:
- `acme/prod`
- `acme/dev`
- `acme/staging`

## New tenant/workspace bootstrap behavior

- Existing scopes are backfilled by migration `023_rbac_scope_bootstrap.sql`.
- New scopes are auto-seeded from `default/default` RBAC templates during successful RBAC authentication in that scope.

## First-admin bootstrap (break-glass)

If a brand new scope has no user with `users:manage_roles`, role assignment cannot be done via API yet.

Use one-time SQL to assign an initial admin role:

```sql
INSERT INTO user_workspace_roles (
  tenant_id,
  workspace,
  user_id,
  role_id,
  granted_by,
  granted_at
)
VALUES ('acme', 'prod', 'u_admin', 'admin', 'bootstrap', now())
ON CONFLICT (tenant_id, workspace, user_id)
DO UPDATE SET
  role_id = EXCLUDED.role_id,
  granted_by = EXCLUDED.granted_by,
  granted_at = now();
```

After this, continue management using API endpoints only.

## Related references

- Permission matrix: `docs/06_api/rbac_permissions.md`
- Full API details: `docs/06_api/api_reference.md`
