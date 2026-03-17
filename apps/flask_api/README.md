# Flask API

Versioned Flask API for McKaySystem.

This app is the backend read/write surface used by the Next.js frontend in
`apps/frontend/`. Product-facing finding reads must come from Postgres through
`finding_current`, with every query scoped by `tenant_id` and `workspace`.

## Responsibilities

- expose versioned REST endpoints under `/api/v1/`
- authenticate users and session-based frontend requests
- enforce tenant/workspace isolation
- read current findings from Postgres
- apply lifecycle, team, RBAC, and governance actions
- publish OpenAPI metadata for client discovery

## Architecture

The API is organized into Flask blueprints so each domain remains isolated and
testable.

Key runtime behavior in `flask_app.py`:

- registers all blueprints
- exposes both `/api/*` and `/api/v1/*` route forms
- enforces CORS, schema gate, rate limiting, and auth
- generates OpenAPI at `/openapi.json` and `/api/openapi.json`

## Directory structure

```text
apps/flask_api/
  flask_app.py
  blueprints/
  utils/
```

Important blueprint areas:

- `auth.py` - login, logout, session identity
- `users.py` - user administration
- `api_keys.py` - API key management
- `findings.py` - finding reads and governance updates
- `recommendations.py` - recommendation endpoints
- `remediations.py` - remediation flows
- `lifecycle.py` - ignore, snooze, resolve actions
- `runs.py` - latest run and run diffs
- `teams.py` - team CRUD and membership
- `sla_policies.py` - SLA policy management
- `groups.py` - grouped finding views
- `facets.py` - filter facets and audit support
- `health.py` - health and DB readiness

## Local development

Start the API directly:

```bash
python -m apps.flask_api.flask_app
```

Alternative Flask CLI flow:

```bash
flask --app apps.flask_api.flask_app run --host 0.0.0.0 --port 5000
```

Production example:

```bash
gunicorn -w 4 -b 0.0.0.0:5000 apps.flask_api.flask_app:app
```

## Environment

Required:

- `DB_URL` - PostgreSQL connection string

Common API settings:

- `API_VERSION` - version prefix, defaults to `v1`
- `API_BEARER_TOKEN` - optional bearer auth for non-session routes
- `API_CORS_ALLOWED_ORIGINS` - allowed browser origins
- `API_CORS_ALLOW_CREDENTIALS` - enable cookie credentials for frontend calls
- `API_ENFORCE_SCHEMA_GATE` - fail fast when DB schema is behind local migrations
- `API_RATE_LIMIT_RPS` and `API_RATE_LIMIT_BURST` - lightweight rate limiting

## Frontend integration

The Next.js frontend expects:

- API base such as `http://127.0.0.1:5000/api/v1`
- cookie-capable CORS when frontend and backend are on different origins
- `tenant_id` and `workspace` on all scoped requests

Recommended local pairing:

1. Run Flask on `http://127.0.0.1:5000`
2. Set `apps/frontend/.env.local` to `NEXT_PUBLIC_API_URL=http://127.0.0.1:5000/api/v1`
3. Run the frontend with `npm run dev`

## API contracts

- Findings must be queried from `finding_current` only.
- Never drop tenant/workspace filtering.
- Use parameterized SQL only.
- Avoid `SELECT *` in API code.

## OpenAPI and health

- `GET /openapi.json`
- `GET /api/openapi.json`
- `GET /api/version`
- `GET /api/health/db`

## Testing

```bash
python -c "from apps.flask_api.flask_app import app; print(app.url_map)"
pytest tests/ -v
```
