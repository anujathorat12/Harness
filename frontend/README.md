# Harness Admin/Operator UI

A lightweight React + Vite + TypeScript app for operating the BYOA Agent
Harness Platform visually. It is a plain REST client of the backend API in
`../harness/` — it has no server of its own beyond serving static files, no
database, and enforces no permissions itself. Every privileged action is
checked again, authoritatively, by the backend. See
`../ARCHITECTURE.md` § "Authentication / authorization" and `../SECURITY.md`.

## Pages

- **Dashboard** — platform-wide counts (agents, sessions by status, pending
  approvals) plus recent policy decisions and audit events.
- **Agents** — list/register agents, view versions, resource limits, and
  attached policy; submit a task to an agent.
- **Sessions** — list/filter sessions, view a session's task result and its
  full list of governed actions.
- **Policies** — list/create policies and versions (raw YAML/JSON — no
  separate policy language), attach a version to an agent or session.
- **Approvals** — pending human-in-the-loop approvals with Allow/Deny.
- **Audit** — filterable, backend-authoritative audit trail.
- **System** — health, database status, concurrency counters, sandbox
  runtime availability. Never displays API key values.

## Run it

Against a locally running backend (`uvicorn harness.main:app` or
`docker compose up harness` from the repo root):

```bash
npm install
npm run dev
```

Open the printed `localhost:5173` URL and paste an API key (see the
backend's README for the dev-default keys — one per role).

## Configuration

`VITE_API_BASE_URL` (see `.env`) — the backend's base URL. Defaults to
`http://localhost:8000`.

## Build

```bash
npm run build   # outputs to dist/
```

The Docker image (`Dockerfile`) builds this and serves it via a plain nginx
— see the repo root `docker-compose.yml`'s `frontend` service.

## Testing

Manually verified in this build: production build succeeds cleanly (`tsc -b
&& vite build`, zero errors), and every endpoint each page calls was
exercised directly against the live backend to confirm response shapes
match `src/types.ts`. There is no automated browser/component test suite
yet — a real next step, listed honestly in the main README's "Known
limitations," not silently skipped.
