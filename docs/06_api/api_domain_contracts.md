# API Domain Contracts

Status: Canonical  
Last reviewed: 2026-03-18

## Purpose

This document defines the canonical domain contracts for the tier-1 public API
surfaces.

The goal is to stabilize:

- object meaning
- field meaning
- enum meaning
- compatibility aliases
- what each endpoint family is allowed to represent

This document complements:

- [api_reference.md](/McKaySystem/docs/06_api/api_reference.md)
- [api_inventory_matrix.md](/McKaySystem/docs/06_api/api_inventory_matrix.md)
- [glossary.md](/McKaySystem/docs/00_overview/glossary.md)
- [product_surface_contract.md](/McKaySystem/docs/00_overview/product_surface_contract.md)

---

## Global rules

These rules apply to all canonical public domain contracts:

1. Canonical public base path is `/api/v1`.
2. `/api` remains a compatibility base only.
3. All scoped domain objects are constrained by `tenant_id` and `workspace`
   unless the domain contract explicitly documents tenant-wide behavior.
4. Additive fields are allowed in `v1`.
5. Breaking field removal, silent rename, or semantic reassignment is not
   allowed in `v1` without an explicit compatibility or migration plan.
6. Public semantics take precedence over implementation convenience.

Pagination rules for paginated public routes:

- request parameters:
  - `limit`
  - `offset`
- response fields:
  - `items`
  - `total`
  - `limit`
  - `offset`
- empty result sets should return `ok=true` with empty `items`
- invalid pagination input should return the standard `bad_request` error
  envelope

---

## Findings Contract

Canonical purpose:

- represent the authoritative detection layer

Canonical endpoints:

- `GET /api/v1/findings`
- `GET /api/v1/findings/sla/breached`
- `GET /api/v1/findings/aging`
- `GET /api/v1/findings/aggregates`
- `GET /api/v1/facets`

Canonical object meaning:

- one finding is one authoritative detected signal in `finding_current`
- findings are resource-centric and fingerprinted
- findings may overlap other findings on the same resource or savings theme
- findings may contain checker advice, but checker advice does not make a
  finding a recommendation

Canonical semantic fields:

- `fingerprint`
  - stable identifier of the detected signal
- `check_id`
  - canonical checker rule identifier
- `title`
  - human-readable detection summary
- `severity`
  - detection severity, not action priority
- `service`
  - cloud service classification
- `category`
  - detection category classification
- `status`
  - raw finding lifecycle state from the read model
- `effective_state`
  - normalized finding lifecycle for filtering and governance
- `estimated_monthly_savings`
  - checker-estimated savings signal at finding level
  - may overlap with other findings
  - must not be treated as the canonical customer-facing potential-savings KPI
- `advice` or normalized checker-advice field
  - explanatory remediation guidance
  - not a package-native action object

Field meaning constraints:

- finding savings are detection-layer estimates
- finding severity does not imply recommendation priority
- finding absence of recommendation is valid and expected

Compatibility notes:

- findings may expose legacy governance or lifecycle fields as long as their
  meaning stays in the detection/governance layer
- the contract does not require every finding to map to a recommendation

Explicit non-goals:

- findings are not the deduplicated action layer
- findings are not the canonical source for potential savings KPI

---

## Recommendations Contract

Canonical purpose:

- represent the curated action layer

Canonical endpoints:

- `GET /api/v1/recommendations`
- `GET /api/v1/recommendations/composite`
- `POST /api/v1/recommendations/estimate`
- `POST /api/v1/recommendations/preview`

Canonical object meaning:

- a recommendation is an actionable object derived from one or more findings
- a recommendation must add value beyond a single raw finding through at least
  one of:
  - deduplication
  - grouping
  - package context
  - suppression
  - owner hinting
  - actionability semantics
  - package savings ownership

Canonical semantic fields:

- `fingerprint`
  - leaf-finding lineage identifier
  - valid on item views
  - not sufficient by itself to define package identity
- `recommendation_type`
  - normalized action category
- `checker_advice`
  - explanatory carry-through from the underlying finding
  - subordinate to recommendation semantics
- `confidence`
  - current compatibility confidence score
- `confidence_model`
  - canonical explicit confidence breakdown
- `graph_package`
  - package/grouping context when applicable
- `is_primary_package_savings_owner`
  - whether this item carries package savings ownership
- `suppressed_by_fingerprint`
  - overlap-suppression marker for non-owning leaf items
- `effective_estimated_monthly_savings`
  - canonical item-level actionable savings after package suppression semantics
- `effective_estimated_annual_savings`
  - annualized effective actionable savings

Canonical behavioral rules:

1. Coverage gaps and permission gaps must not be recommendations.
2. Recommendations may be fewer than findings.
3. Suppressed recommendation items may exist for explainability, but only
   primary owners contribute to package-owned savings.
4. Recommendation candidates are not the same thing as recommendations.

Compatibility notes:

- some current internal naming still uses `recommendations` for recommendation
  candidates in KPI contexts
- `GET /api/v1/recommendations?view=items` remains compatible with leaf-item
  semantics
- `GET /api/v1/recommendations?view=packages` is the more package-native action
  expression when package data exists

Explicit non-goals:

- recommendations are not just a second findings table
- raw checker eligibility alone does not define final recommendation meaning

---

## Remediations Contract

Canonical purpose:

- represent requested, approved, executed, and verified action outcomes

Canonical endpoints:

- `GET /api/v1/remediations`
- `GET /api/v1/remediations/impact`
- `POST /api/v1/remediations/request`
- `POST /api/v1/remediations/approve`
- `POST /api/v1/remediations/reject`

Canonical object meaning:

- a remediation is an action workflow object tied to a finding- or
  recommendation-derived opportunity
- remediation status is workflow state, not detection state
- remediation impact is the outcome layer and is the canonical source for
  realized savings semantics

Canonical semantic fields:

- `action_id`
  - canonical remediation workflow identifier
- `action_type`
  - normalized remediation action family
- `status`
  - request/approval/execution workflow state
- `verification_status`
  - outcome verification state
- `baseline_estimated_monthly_savings`
  - original estimated savings at action creation/impact baseline
- `realized_monthly_savings`
  - verified realized savings amount when available
- `outcome_status`
  - normalized realization state
- `outcome_label`
  - human-readable realization state
- `realization_band`
  - normalized outcome bucket
- `estimated_not_realized_monthly_savings`
  - remaining unrealized estimate

Canonical behavioral rules:

1. Realized savings must come from the remediation outcome layer.
2. Remediation workflow state must not be conflated with finding lifecycle.
3. A remediation may exist even if no savings have yet been verified.
4. Realized savings and potential savings must stay separate.

Compatibility notes:

- baseline savings remain useful for comparison, but customer-facing proof of
  value belongs to realized outcome fields

---

## Runs and Coverage Contract

Canonical purpose:

- represent assessment completeness, trust, and run freshness

Canonical endpoints:

- `GET /api/v1/runs/latest`
- `GET /api/v1/runs/diff/latest`
- coverage-adjacent read surfaces that explain assessed versus degraded scope

Canonical object meaning:

- runs describe collection/build execution state for a scope
- coverage describes what could and could not be assessed reliably
- permission gaps belong to coverage/trust, not recommendations

Canonical semantic fields:

- `run_id`
  - canonical run identifier
- `status`
  - run status
- `started_at`, `completed_at`
  - run timing and freshness fields
- coverage percent / assessed counts / failed counts
  - trust and assessment completeness indicators
- permission-gap counters
  - explain missing visibility

Canonical behavioral rules:

1. Coverage metrics are trust metrics, not savings metrics.
2. Access-denied and permission-gap signals belong to coverage.
3. Run diffs explain platform movement, not customer value by themselves.

Compatibility notes:

- coverage may still be composed from multiple underlying routes and views, but
  its product meaning must remain trust-oriented

---

## KPI Contract

Canonical purpose:

- provide customer-facing value and trust summaries without collapsing distinct
  semantics together

Canonical endpoint:

- `GET /api/v1/kpis/initial-value`

Canonical KPI families:

- `findings`
- `recommendations`
- `potential_savings`
- `realized_savings`
- `coverage`
- `trend`

Canonical family meanings:

- `findings`
  - detection-layer count and raw estimated-savings breadth
- `recommendations`
  - recommendation candidates under current eligibility policy
  - compatibility name retained on the wire
- `potential_savings`
  - deduplicated action-layer savings KPI
  - canonical customer-facing savings KPI
- `realized_savings`
  - verified remediation-outcome savings KPI
- `coverage`
  - trust/completeness KPI
- `trend`
  - movement between latest comparable ready runs

Canonical field meaning constraints:

- `findings.estimated_monthly_savings`
  - detected savings only
  - may overlap
- `recommendations.*`
  - recommendation-candidate semantics unless and until the wire contract is
    versioned more explicitly
- `potential_savings.estimated_monthly_savings`
  - actionable, deduplicated, package-owner-aware estimate
- `realized_savings.realized_total_monthly_savings`
  - verified outcome metric

Canonical behavioral rules:

1. The main customer-facing savings KPI is `potential_savings`.
2. `findings` must not be relabeled as final recommendations.
3. Recommendation candidates and recommendations must stay distinguishable in
   docs and product copy.
4. Coverage must never be mixed into a savings total.

Compatibility notes:

- the `recommendations` family name remains for wire compatibility
- its documented meaning is recommendation candidates

---

## Tenant Administration Contract

Canonical purpose:

- represent tenant-scoped administrative control over workspaces, inherited
  tenant access, and tenant-wide administrative visibility

Canonical endpoints:

- `GET /api/v1/tenant-admin/workspaces`
- `POST /api/v1/tenant-admin/workspaces`
- `PUT /api/v1/tenant-admin/workspaces/{target_workspace}`
- `GET /api/v1/tenant-admin/role-bindings`
- `GET /api/v1/tenant-admin/users`
- `GET /api/v1/tenant-admin/audit`
- `PUT /api/v1/tenant-admin/users/{user_id}/role-binding`
- `DELETE /api/v1/tenant-admin/users/{user_id}/role-binding`

Canonical object meaning:

- tenant administration is distinct from workspace administration
- workspace authority never implies tenant authority
- inherited tenant access is tenant-level policy that can resolve at runtime
  and across future workspaces

Canonical semantic fields:

- workspace registry fields
  - identify managed workspace scope and lifecycle state
- inherited access binding fields
  - identify tenant-level access policy for a user and source workspace
- tenant directory fields
  - identify user presence and access posture across the tenant
- audit event fields
  - identify administrative changes at tenant level

Canonical behavioral rules:

1. Only tenant admin or stronger authority can manage tenant administration.
2. Workspace admin remains workspace-scoped.
3. Inherited tenant access is distinct from direct workspace assignment.
4. Future-workspace access policy is part of tenant administration, not normal
   workspace administration.

Compatibility notes:

- some API responses still preserve compatibility aliases such as
  `future_workspace_binding`
- canonical product wording is `inherited tenant access`

---

## Use Of This Document

Use this document to keep the tier-1 API domains consistent in meaning.

It should stay focused on:

- public object semantics
- stable field meaning
- compatibility notes that still matter to clients

It should not become an implementation diary.
