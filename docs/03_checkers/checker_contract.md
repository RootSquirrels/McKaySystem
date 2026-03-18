# Checker contract

Status: Canonical  
Last reviewed: 2026-03-19

This document defines how checkers behave across the platform.

## Purpose

A checker scans a cloud service and emits **Findings**.

## Invariants

A checker MUST:

- never crash the entire run because one resource call failed
- be deterministic for identical observed input
- emit findings with stable identifiers (`issue_key` / fingerprint inputs)
- separate **signals** from **interpretation**
- use shared helpers and avoid redundant code

A checker MUST NOT:

- mutate storage directly
- claim exact cost unless it is sourced from CUR enrichment
- hide `AccessDenied` errors

## IAM and error handling

For AWS API calls:

- Missing configuration (for example, no lifecycle) -> `fail` or `warn`
  depending on severity
- `AccessDenied` -> `info` with clear remediation
- Unexpected errors -> raise unless there is a well-justified fallback

## Required finding fields

Each finding must include:

- `check_id`
- `severity`
- `resource_id` and/or `resource_arn` when available
- `issue_key`
- best-effort cost fields when applicable

See `04_schemas/finding_schema.md` for the schema contract.
