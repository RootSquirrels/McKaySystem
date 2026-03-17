# Backend Boundary

This subtree owns the Postgres-backed backend boundary used by the Flask API.

## Scope

Primary files:

- `apps/backend/db.py` - connection and query helpers
- `apps/backend/db_migrate.py` - migration entrypoint and schema checks
- `apps/flask_api/` - API layer consuming the backend DB boundary

## Source of truth

For product reads, Postgres is the source of truth.

Rules:

- query current findings from `finding_current` only
- scope every query by `tenant_id` and `workspace`
- use parameterized SQL only
- keep lifecycle precedence in the database view layer, not Python

## Operational responsibilities

- manage DB connectivity for the API
- apply migrations before serving traffic
- fail fast on schema mismatch when schema gate is enabled
- keep backend deployment lifecycle independent from worker execution

## Typical commands

Run migrations:

```bash
python -m apps.backend.db_migrate
```

Start the API:

```bash
python -m apps.flask_api.flask_app
```

## Notes for contributors

- Do not move lifecycle logic out of the DB read model.
- Do not introduce unscoped queries.
- Treat worker outputs and DB state as separate concerns: workers produce data,
  the backend serves current application state.
