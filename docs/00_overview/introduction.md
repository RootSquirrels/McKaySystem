# Introduction

Status: Canonical  
Last reviewed: 2026-02-01

This project is an AWS-focused FinOps platform.

At a high level:

1. **Checkers** scan AWS APIs and emit **Findings**.
2. Workers write findings to Parquet, then ingest them into Postgres.
3. The **Flask API** serves scoped data from Postgres to the React frontend.
4. The **Correlation Engine** combines related signals into stronger findings.
5. The **CUR pipeline** can enrich findings with real cost data when a Cost and
   Usage Report is available.

Optional JSON export still exists for compatibility and operational inspection,
but it is no longer the primary web application path.

If you are new:
- read `00_overview/glossary.md`
- then `01_architecture/architecture.md`
- then `02_pipeline/pipeline_overview.md`
