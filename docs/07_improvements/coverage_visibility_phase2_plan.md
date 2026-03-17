# Coverage Visibility Phase 2 Plan

Status: Proposed  
Last reviewed: 2026-03-17

## Objective

Build the second phase of coverage visibility so the platform can move from
"latest run visibility exists" to "coverage behaves like a first-class
operational surface."

Phase 1 established the baseline:

- structured coverage rows and issues are persisted
- latest run coverage summary is exposed in API
- checker and issue drilldown exists
- frontend shows degraded coverage and a dedicated details page

Phase 2 should deepen that baseline without entangling findings,
recommendations, or IAM remediation semantics too early.

This phase is intentionally narrower than the earlier draft. It focuses on:

- filters on current coverage endpoints
- service and account rollups
- coverage history
- regression detection
- coverage page UI improvements

The following items are explicitly postponed to a later phase:

- finding-level coverage context
- recommendation-level coverage context
- permission-aware diagnostics and IAM guidance

---

## Goals

### Functional goals

- compare coverage across runs and expose regressions
- add richer filters and grouped scorecards on coverage APIs and UI
- make it easy to isolate blind spots by service, account, region, and checker
- strengthen coverage as an operational monitoring surface

### Non-functional goals

- preserve determinism
- keep all reads scoped by `tenant_id` and `workspace`
- keep current Phase 1 APIs backward compatible
- avoid introducing semantics that overstate what coverage can say about one
  individual finding

### Non-goals

- no replacement of the Phase 1 coverage model
- no finding-level confidence labels in this phase
- no recommendation confidence blending in this phase
- no IAM remediation assistant behavior in this phase

---

## Scope

## 1. Endpoint filters on current coverage APIs

### 1.1 Problem

The Phase 1 details page is useful, but users still need faster ways to answer:

- show me only permission failures
- show me only one region or account
- show me only failed or skipped checkers

### 1.2 Deliverables

- richer API filters on checker and issue endpoints
- pagination support where needed
- frontend filter controls on the coverage page

### 1.3 Target endpoints

Enhance:

- `GET /api/runs/latest/coverage/checkers`
- `GET /api/runs/latest/coverage/issues`

Add filter support for:

- `status`
- `service`
- `region`
- `account_id`
- `checker_id`
- `issue_type`
- `limit`
- `offset`

### 1.4 Query design rules

All queries must:

- filter by `tenant_id` and `workspace`
- avoid `SELECT *`
- use indexed columns
- remain bounded and paginated where row counts can grow

---

## 2. Service and account rollups

## 2.1 Problem

Users need quick answers to:

- which services are most degraded
- which accounts or regions are most blind
- where permission gaps are concentrated

### 2.2 Deliverables

- service summary endpoint
- account or account-region summary endpoint
- grouped summary cards on the coverage page

### 2.3 Proposed endpoints

- `GET /api/runs/latest/coverage/services`
- `GET /api/runs/latest/coverage/accounts`

Suggested summary fields:

- `targets_total`
- `assessed_total`
- `assessment_failed`
- `skipped_total`
- `not_assessed_total`
- `permission_gap_count`
- `coverage_pct`
- `coverage_status`

### 2.4 Data model strategy

Start with API-side aggregation from existing coverage tables.

Only add persistent rollup tables later if:

- grouped queries become expensive
- access patterns stabilize

Possible later tables:

- `run_coverage_service_summary`
- `run_coverage_account_summary`

---

## 3. Coverage history

## 3.1 Problem

Phase 1 shows only the latest run clearly. Users still cannot answer:

- did coverage get worse this week
- has a service been flaky for several runs
- when did permission gaps start

### 3.2 Deliverables

- run history endpoint for coverage summary by run
- frontend history section on the coverage page

### 3.3 Proposed endpoint

- `GET /api/runs/coverage/history`

Suggested filters:

- `limit`
- `status`
- `date_from`
- `date_to`

### 3.4 History output

For each run, expose:

- `run_id`
- `run_ts`
- `coverage_pct`
- `coverage_status`
- `targets_total`
- `assessment_failed`
- `permission_gap_count`

---

## 4. Regression detection

## 4.1 Problem

Historical data alone is not enough. Users need clear signals when coverage
actually regressed.

### 4.2 Deliverables

- regression endpoint comparing latest run to previous ready run
- regression cards or summary table in the coverage page
- service-level and checker-level regression counts where feasible

### 4.3 Proposed endpoint

- `GET /api/runs/coverage/regressions/latest`

### 4.4 Regression semantics

Start with deterministic regression rules:

- `coverage_pct` decreased
- `assessment_failed` increased
- `permission_gap_count` increased
- a service or checker moved from healthy or partial to degraded or failed

### 4.5 Severity guidance

To avoid overreacting to noise, classify regressions into:

- `minor`
- `meaningful`
- `critical`

Initial severity can be based on fixed thresholds, not heuristics.

---

## 5. Coverage page UI improvements

## 5.1 Deliverables

Add to the existing coverage page:

- filter toolbar
- service summary cards
- account or region rollups
- history section
- regression section for latest vs previous run

### 5.2 UX principles

- keep coverage as a first-class nav surface
- make filters shareable through query params
- keep degraded states visible even when the page loads otherwise successfully
- pair percentages with absolute counts

---

## Explicitly postponed to Phase 3

The following are valuable, but intentionally out of scope for Phase 2 because
they introduce higher semantic risk or a broader product surface:

### 1. Finding-level coverage context

Reason for postponement:

- users may overread local coverage hints as definitive truth about one
  individual finding
- scoping rules and wording need to be especially careful

### 2. Recommendation-level coverage context

Reason for postponement:

- easy to confuse recommendation confidence with scan coverage confidence
- should remain additive and carefully defined when introduced

### 3. Permission-aware diagnostics

Reason for postponement:

- grouping by missing action
- ranking blockers
- surfacing permission families
- IAM remediation hints

This starts to become a mini-feature set of its own and should not ride inside
the same implementation wave unless the rest of Phase 2 is already stable.

---

## API plan

## 1. New endpoints

- `GET /api/runs/latest/coverage/services`
- `GET /api/runs/latest/coverage/accounts`
- `GET /api/runs/coverage/history`
- `GET /api/runs/coverage/regressions/latest`

## 2. Existing endpoint enhancements

Enhance:

- `GET /api/runs/latest/coverage/checkers`
- `GET /api/runs/latest/coverage/issues`

with filter support for:

- `status`
- `service`
- `region`
- `account_id`
- `checker_id`
- `issue_type`
- `limit`
- `offset`

---

## Frontend plan

## 1. Coverage page

Add:

- filter controls
- service summary section
- account or region summary section
- run history section
- regression section

## 2. Navigation

Keep the existing coverage page as the central operational view rather than
spreading these new capabilities across findings or recommendations yet.

---

## Rollout

## Phase 2A: Filters and rollups

Deliverables:

- filters on current checker and issue endpoints
- service summary endpoint
- account summary endpoint
- coverage page filter toolbar and grouped summary cards

Exit criteria:

- users can isolate degraded scopes quickly

## Phase 2B: History and regressions

Deliverables:

- coverage history endpoint
- regression endpoint
- coverage page history and regression sections

Exit criteria:

- users can identify newly degraded coverage and scan regressions

## Phase 3: Context and diagnostics

Deferred deliverables:

- finding-level coverage context
- recommendation-level coverage context
- permission-aware diagnostics

---

## Testing plan

## 1. API tests

Add tests for:

- filter behavior on checker and issue endpoints
- service and account summary correctness
- history output correctness
- regression output correctness
- no cross-tenant leakage

## 2. Frontend tests

If harness exists or is introduced:

- filter toolbar state and query param sync
- service and account rollup rendering
- history and regression section rendering

## 3. Summary and regression tests

Add tests for:

- deterministic regression calculations
- grouped rollup correctness
- pagination and filter behavior under mixed statuses

---

## Risks and mitigations

### Risk: too many summary tables

Mitigation:

- start with API-side aggregation
- add persistent summaries only when justified by real query cost

### Risk: history endpoints get expensive

Mitigation:

- paginate
- cap default history window
- rely on indexed run-scoped summary tables

### Risk: users overreact to minor regressions

Mitigation:

- classify regression severity
- distinguish meaningful regressions from small percentage drift

### Risk: Phase 2 grows into Phase 3

Mitigation:

- keep finding context, recommendation context, and permission diagnostics out
  of this implementation wave

---

## Recommended implementation order

1. Add filters to checker and issue APIs.
2. Add service and account rollup endpoints.
3. Update the coverage page with filters and grouped summaries.
4. Add the history endpoint.
5. Add the regression endpoint.
6. Add history and regression UI to the coverage page.

---

## Acceptance criteria

Phase 2 is successful when:

- users can filter coverage details by status, service, region, account, and
  issue type
- the platform can show grouped service and account coverage scorecards
- the platform can show coverage history between runs
- the platform can detect and display meaningful regressions
- all new reads remain tenant/workspace safe and deterministic
