# Frontend

Next.js 16 frontend for McKaySystem, built with React 19, TypeScript, and
TanStack Query.

This app is the primary web client for the platform. It talks to the Flask API
under `apps/flask_api/` and expects tenant/workspace-scoped API access.

## Stack

- Next.js App Router
- React 19
- TypeScript
- TanStack Query
- React Hook Form + Zod

## Local development

Install dependencies and start the dev server:

```bash
npm install
npm run dev
```

Default URL:

```text
http://localhost:3000
```

## Required environment

Create `apps/frontend/.env.local` with:

```bash
NEXT_PUBLIC_API_URL=http://127.0.0.1:5000/api/v1
```

Notes:

- `NEXT_PUBLIC_API_URL` must point to the Flask API base, not just the host.
- The current checked-in local example points to a hosted API. For local
  full-stack development, switch it to your local Flask instance.

## Frontend-backend contract

The frontend uses:

- cookie-based auth via `fetch(..., { credentials: "include" })`
- tenant/workspace scoping stored in browser `sessionStorage`
- versioned API routes such as `/api/v1/auth/*`, `/api/v1/findings`,
  `/api/v1/recommendations`, and `/api/v1/users`

The frontend automatically appends `tenant_id` and `workspace` query params
from the stored scope when they are not explicitly provided.

## Main routes

- `/login` - session login and tenant/workspace bootstrap
- `/findings` - findings table and lifecycle actions
- `/recommendations` - recommendation views
- `/users` - user administration

## Important files

- `src/app/` - App Router pages
- `src/hooks/` - API-facing hooks
- `src/lib/api/client.ts` - shared API client
- `src/lib/scope.ts` - tenant/workspace persistence
- `src/components/providers/query-provider.tsx` - React Query provider

## Recommended local workflow

Run the backend first:

```bash
python -m apps.flask_api.flask_app
```

Then start the frontend:

```bash
npm run dev
```

If the frontend cannot authenticate or fetch data, check:

- `NEXT_PUBLIC_API_URL`
- Flask CORS configuration
- browser cookies for the API origin
- matching `tenant_id` and `workspace`

## Quality checks

```bash
npm run lint
npm run build
```
