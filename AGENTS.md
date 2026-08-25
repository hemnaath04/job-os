# AGENTS.md

## Cursor Cloud specific instructions

This is a pnpm + Turborepo (Node) and `uv` (Python) monorepo for **job.os**. The
canonical local-run commands live in `README.md` ("Running it locally") and
`docs/SETUP.md`; the notes below only cover Cloud-agent-specific, non-obvious
setup that those docs do not.

### Services (all local)

| Service | Dir | Dev command | Port |
| --- | --- | --- | --- |
| API (FastAPI) | `apps/api` | `uv run uvicorn job_os.main:app --reload --port 8000` | 8000 (`/docs` in dev) |
| Web (Next.js) | `apps/web` | `pnpm dev` (from a login shell) | 3000 |
| PostgreSQL 16 + pgvector | — | see "Start Postgres" below | 5432 |

Appwrite, Redis, Firecrawl, Anthropic, R2 etc. are all optional and degrade
gracefully; `NEXT_PUBLIC_PIPELINE_BACKEND`/`NEXT_PUBLIC_WORKSPACE_BACKEND` are
kept at `legacy`, so the app runs entirely off the local API + Postgres.

### Start Postgres (not auto-started on boot)

The update script installs deps but does not start services. Postgres must be
started each session before running the API or its migrations:

```bash
sudo pg_ctlcluster 16 main start
```

Local DB is `jobos`, role `postgres`/`postgres`, with the `pgcrypto` and `vector`
extensions already created. If the cluster or DB is ever missing, recreate with:
`sudo -u postgres psql -c "CREATE DATABASE jobos;"` then
`sudo -u postgres psql -d jobos -c "CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS vector;"`.

### Env files (gitignored, persisted in the VM snapshot)

- `/.env` — read by the API settings loader. Sets `DATABASE_URL` to the local
  Postgres, and `APP_ENV=development` + `ALLOW_ANONYMOUS_DEV_USER=true`.
- `/apps/web/.env.local` — read by Next.js. Holds dummy Clerk keys plus
  `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` and the `legacy` backend flags.

If either file is missing on a fresh VM, recreate it (values above / see
`.env.example`). Do not commit them.

### Auth caveat (important)

- The **API** mints a shared anonymous `dev-local` user because
  `APP_ENV=development` + `ALLOW_ANONYMOUS_DEV_USER=true`, so you can hit
  `http://localhost:8000/api/v1/...` with no token. This is how to exercise core
  functionality (e.g. `POST /api/v1/jobs/manual` then `POST /api/v1/applications`).
- The **web UI**'s protected pages (`/applications`, `/dashboard`, and most
  `/api/backend/*`) require a **real Clerk session**. The dummy Clerk keys in
  `apps/web/.env.local` only let `next dev` boot and render the public landing
  page (`/`); sign-in and the app shell need real `CLERK_*` keys.

### Node version caveat (web lint/test/build)

The exec-daemon's default `node` is 22.14, but the web test command
(`node --test "src/**/*.test.ts"`, run by `pnpm --filter @job-os/web test`) needs
Node's unflagged TypeScript stripping (Node >= 22.18), matching CI's
`node-version: 22`. A current Node (22.23.2, via nvm) is symlinked into
`~/.local/bin/node`, which precedes `/exec-daemon` on PATH, and `corepack`/`pnpm`
are enabled for it. Run web commands from a **login shell** (e.g. `bash -lc '...'`)
so `node`/`pnpm` resolve correctly.

### uv

`uv` is installed at `~/.local/bin` (added to PATH in `~/.bashrc`). Run `uv`
commands for the API from `apps/api`.

### Lint / test / build

Standard scripts are in `package.json` and `apps/api/pyproject.toml`:
- API: `uv run pytest -q` (default excludes `slow` Tectonic-render tests),
  `uv run ruff check src tests`, `uv run mypy src`.
- Python lint/type is enforced in CI by `scripts/lint-ratchet.sh` (a ratchet, not
  zero: current baselines are ruff 36 / mypy 71 — CI fails only if counts rise).
- Web: `pnpm --filter @job-os/web lint | typecheck | test`.

### PDF rendering

`tectonic`/`typst` binaries are **not** installed here. PDF export and the
`slow`-marked render tests degrade/skip without them; everything else works. See
`apps/api/Dockerfile.vercel` for the pinned Tectonic install if needed.
