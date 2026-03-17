# Introduction

Status: Canonical  
Last reviewed: 2026-02-01

This project is an AWS-focused FinOps platform:

1. **Checkers** scan AWS APIs and emit **Findings** (signals).
2. Findings are written to Parquet by workers, then ingested into Postgres for product-facing reads.
3. A **Flask API** serves scoped data from Postgres to the React frontend.
4. A **Correlation Engine** combines multiple signals into higher-confidence meta-findings.
5. A **CUR pipeline** can enrich findings with real costs when Cost & Usage Report data is available.

Optional JSON export still exists for compatibility and operational inspection,
but it is no longer the primary web application path.

If you are new:
- read `00_overview/glossary.md`
- then `01_architecture/architecture.md`
- then `02_pipeline/pipeline_overview.md`
