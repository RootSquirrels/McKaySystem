# Building Recommendations

Status: Canonical  
Last reviewed: 2026-02-22

## Purpose

Clarify how recommendations are produced to avoid duplicate philosophies.

In this system:

1. Checkers emit **findings** with factual signals and checker guidance (`advice`).
2. Ingest persists findings into `finding_latest` / `finding_current`.
3. `/api/recommendations*` derives normalized **action plans** from `finding_current`
   using recommendation rules in `apps/flask_api/blueprints/recommendations.py`.
4. When current resource graph data exists, recommendation items may also include
   bounded `graph_package` context derived from `resource_graph_*_current` to
   show related resources, likely blast radius, package title, and dependency
   checklist.
5. When multiple recommendation items fall into the same graph-backed package
   cluster, the API may assign one primary savings owner and expose suppression
   metadata so clients can avoid double-counting overlapping savings.
6. `/api/recommendations?view=packages` can return one package-native object per
   grouped cluster, with one primary recommendation plus member recommendations.

There is currently no separate worker "recommendations build step" table.

## Build flow (authoritative)

1. Run engine/checkers:
```bash
python -m apps.worker.runner --tenant acme --workspace prod
```
2. Ingest run output:
```bash
python -m apps.worker.ingest_parquet --manifest <path-to-run-manifest.json>
```
3. Query recommendations API:
```bash
curl "http://localhost:8000/api/recommendations?tenant_id=acme&workspace=prod&state=open&order=savings_desc"
```

## Windows PowerShell quick check

```powershell
$base = "http://localhost:8000"
$tenant = "acme"
$workspace = "prod"

Invoke-RestMethod `
  -Uri "$base/api/recommendations?tenant_id=$tenant&workspace=$workspace&state=open&order=savings_desc&limit=20" `
  -Method GET
```

Package-native view:

```powershell
Invoke-RestMethod `
  -Uri "$base/api/recommendations?tenant_id=$tenant&workspace=$workspace&state=open&order=savings_desc&view=packages&limit=20" `
  -Method GET
```

## Contract split (do not mix)

1. `finding.payload.advice`:
   - free text from checker
   - explanatory
   - not workflow-contract
2. `recommendations API item`:
   - normalized plan (`recommendation_type`, `action_type`, `target`, `priority`, `requires_approval`)
   - optional bounded `graph_package` context for related-resource packaging
     including package semantics such as `package_kind`, `package_title`,
     `package_reason`, `related_services`, and `dependency_checklist`
   - optional package-level savings metadata such as:
     - `is_primary_package_savings_owner`
     - `suppressed_by_fingerprint`
     - `effective_estimated_monthly_savings`
     - `graph_package.package_estimated_monthly_savings`
     - `graph_package.savings_owner_fingerprint`
   - workflow-contract for queueing/approval/remediation
3. `recommendations API package view item`:
   - one package-native recommendation object per cluster or standalone leaf
   - includes `package_estimated_monthly_savings`, `member_count`,
     `primary_recommendation`, and `member_recommendations`
   - preserves graph package semantics while giving clients one object to act on

## Future option (if needed)

If query-time derivation becomes too expensive, add a materialized
`recommendation_current` read model built during ingest. Until then, API derivation
is the canonical implementation.
