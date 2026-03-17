# Docs

Status: Canonical  
Last reviewed: 2026-02-22

## Quick navigation

- **Start here**: `00_overview/introduction.md`
- **Terms**: `00_overview/glossary.md`
- **Architecture**: `01_architecture/architecture.md`
- **Frontend app**: `../apps/frontend/README.md`
- **Flask API**: `../apps/flask_api/README.md`
- **Backend DB boundary**: `../apps/backend/README.md`
- **Pipeline**: `02_pipeline/pipeline_overview.md`
- **Recommendations build**: `02_pipeline/recommendations_build.md`
- **Checkers contract**: `03_checkers/checker_contract.md`
- **AWS checker catalog**: `03_checkers/aws/README.md`
- **Finding schema**: `04_schemas/finding_schema.md`
- **Operations**: `05_operations/running.md`
- **RBAC bootstrap runbook**: `05_operations/rbac_bootstrap.md`
- **API**: `06_api/api_reference.md`
- **RBAC matrix**: `06_api/rbac_permissions.md`
- **RBAC user/role guide**: `06_api/rbac_user_role_guide.md`
- **Improvement backlog**: `07_improvements/platform_improvement_backlog.md`
- **Coverage implementation plan**: `07_improvements/coverage_visibility_implementation_plan.md`

## Structure

- `00_overview/` - mental model and glossary
- `01_architecture/` - boundaries and decisions
- `apps/frontend/README.md` - Next.js frontend setup and API integration
- `apps/flask_api/README.md` - Flask API runtime and endpoint boundary
- `apps/backend/README.md` - DB source-of-truth and migration boundary
- `02_pipeline/` - CUR, correlation, determinism, and diagrams
- `03_checkers/` - checker contract and service pages
- `04_schemas/` - canonical schema and IDs/fingerprint
- `05_operations/` - running, RBAC bootstrap, permissions, and troubleshooting
- `06_api/` - API reference and smoke testing
- `07_improvements/` - improvement backlog and roadmap notes
