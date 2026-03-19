# API Inventory Matrix

Status: Reference  
Last reviewed: 2026-03-18

## Purpose

This document is the public API inventory and classification matrix.

It classifies each current API domain as one of:

- `stable`
- `beta`
- `compatibility`
- `internal`

Contract rule:

- `/api/v1/*` is the canonical public base
- `/api/*` is the compatibility base that resolves to the same handlers today

A domain should only be marked `stable` when both route shape and field
semantics are solid enough for external clients to depend on them.

Related:

- [api_contract_stabilization_implementation_plan.md](/McKaySystem/docs/07_improvements/api_contract_stabilization_implementation_plan.md)
- [api_reference.md](/McKaySystem/docs/06_api/api_reference.md)

---

## Domain inventory

| Domain | Canonical `/api/v1` surface | Compatibility `/api` surface | Current status | Reason |
|-----|-------------------------------|-------------------------------|----------------|--------|
| Health | Yes | Yes | `stable` | Very small surface with simple semantics |
| Version / OpenAPI discovery | Yes | Yes | `beta` | Route base is stable, but generated schema quality is still evolving |
| Auth | Yes | Yes | `beta` | Important public surface, but token/compat behavior still deserves stronger contract wording |
| Findings read model | Yes | Yes | `beta` | Core product surface, but object contract still needs explicit stabilization |
| Findings governance mutations | Yes | Yes | `beta` | Widely usable, but mutation/error contract is not fully normalized |
| Recommendation items | Yes | Yes | `beta` | Product-critical, but semantics were recently clarified and need formal contract lock |
| Recommendation composite / estimate | Yes | Yes | `beta` | Useful public surface, but derived/action-layer semantics still being formalized |
| Remediations | Yes | Yes | `beta` | Strong base exists, but workflow semantics and response contract should still be locked down |
| KPIs | Yes | Yes | `beta` | Sales-critical, but semantics are still actively being clarified |
| Runs / coverage read model | Yes | Yes | `beta` | Valuable public read model, but large domain with several coverage-specific endpoints still needs domain contract work |
| Groups | Yes | Yes | `beta` | Usable, but still partly tied to internal grouping semantics |
| Facets | Yes | Yes | `beta` | Read-only and useful, but still a supporting surface, not yet formally stabilized |
| Users | Yes | Yes | `beta` | Public admin surface, but inherited tenant access semantics are still relatively new |
| Tenant admin | Yes | Yes | `beta` | Public product admin surface, but newly implemented and should be treated carefully |
| API keys | Yes | Yes | `beta` | Important for integrations, but should be stabilized with stronger auth/contract language |
| Teams | Yes | Yes | `beta` | Public collaboration surface, but still needs stronger contract polish |
| SLA policies | Yes | Yes | `beta` | Admin surface with usable semantics, but not yet formally stabilized |
| Audit query | Yes | Yes | `beta` | Public/admin-support surface, but field contract and export behavior remain to be stabilized |
| Lifecycle mutations | Yes | Yes | `compatibility` | Still uses older payload and error conventions; public, but not the target contract style |

---

## Route inventory by blueprint

### Health

- `GET /health`
- `GET /api/health/db`
- `GET /openapi.json`
- `GET /api/openapi.json`
- `GET /api/version`

### Auth

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Findings

- `GET /api/findings`
- `GET /api/findings/<fingerprint>/graph`
- `GET /api/findings/grouped/category`
- `GET /api/findings/sla/breached`
- `GET /api/findings/aging`
- `GET /api/findings/aggregates`
- `PUT /api/findings/<fingerprint>/owner`
- `PUT /api/findings/<fingerprint>/team`
- `POST /api/findings/<fingerprint>/sla/extend`

### Facets

- `GET /api/facets`

### Groups

- `GET /api/groups`
- `GET /api/groups/<group_key>`

### Recommendations

- `GET /api/recommendations`
- `GET /api/recommendations/composite`
- `POST /api/recommendations/estimate`
- `POST /api/recommendations/preview`

### Remediations

- `POST /api/remediations/request`
- `GET /api/remediations`
- `GET /api/remediations/impact`
- `POST /api/remediations/approve`
- `POST /api/remediations/reject`

### KPIs

- `GET /api/kpis/initial-value`

### Runs / coverage

- `GET /api/runs/latest`
- `GET /api/runs/latest/coverage`
- `GET /api/runs/latest/graph/context`
- `GET /api/runs/latest/coverage/checkers`
- `GET /api/runs/latest/coverage/issues`
- `GET /api/runs/latest/coverage/services`
- `GET /api/runs/latest/coverage/accounts`
- `GET /api/runs/coverage/history`
- `GET /api/runs/coverage/regressions/latest`
- `GET /api/runs/diff/latest`

### Lifecycle

- `POST /api/lifecycle/group/ignore`
- `POST /api/lifecycle/group/resolve`
- `POST /api/lifecycle/group/snooze`
- `POST /api/lifecycle/ignore`
- `POST /api/lifecycle/resolve`
- `POST /api/lifecycle/snooze`

### Users

- `GET /api/users`
- `POST /api/users`
- `GET /api/users/roles`
- `GET /api/users/<user_id>`
- `PUT /api/users/<user_id>`
- `DELETE /api/users/<user_id>`
- `GET /api/users/<user_id>/role`
- `PUT /api/users/<user_id>/role`
- `PUT /api/users/<user_id>/role/tenant`

### API keys

- `GET /api/api-keys`
- `POST /api/api-keys`
- `DELETE /api/api-keys/<key_id>`

### Teams

- `GET /api/teams`
- `POST /api/teams`
- `PUT /api/teams/<team_id>`
- `DELETE /api/teams/<team_id>`
- `GET /api/teams/<team_id>/members`
- `POST /api/teams/<team_id>/members`
- `DELETE /api/teams/<team_id>/members/<user_id>`

### SLA policies

- `GET /api/sla/policies`
- `POST /api/sla/policies`
- `PUT /api/sla/policies/<category>`
- `GET /api/sla/policies/overrides`
- `POST /api/sla/policies/overrides`

### Tenant admin

- `GET /api/tenant-admin/workspaces`
- `POST /api/tenant-admin/workspaces`
- `PUT /api/tenant-admin/workspaces/<target_workspace>`
- `GET /api/tenant-admin/role-bindings`
- `GET /api/tenant-admin/users`
- `GET /api/tenant-admin/audit`
- `PUT /api/tenant-admin/users/<user_id>/role-binding`
- `DELETE /api/tenant-admin/users/<user_id>/role-binding`

---

## Contract decisions

### Canonical public base

Canonical public base:

- `/api/v1`

Compatibility base:

- `/api`

Guideline:

- docs should lead with `/api/v1`
- `/api` remains a compatibility base, not the primary long-term base

### Stability posture

Current posture:

- almost all meaningful product/admin domains are `beta`
- lifecycle is `compatibility`
- none of the major business domains should yet be labeled `stable` until domain contracts and contract tests are completed

This remains intentionally conservative.
