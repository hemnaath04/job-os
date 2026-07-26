# Production deployment

The current production setup separates the latency-sensitive application board
from the heavier Python agent workloads:

- Vercel serves the Next.js web app at `jobs.hemnaath.tech`.
- Appwrite TablesDB serves application-board reads and writes without a sleeping
  backend hop.
- Render serves FastAPI for profile, discovery, PDF, and AI-agent work.
- Neon remains the durable rollback copy while Appwrite changes are dual-written.

## Backend → Render

`render.yaml` is the source of truth for the backend service. It builds the
Docker image in `infra/fly/Dockerfile.api`, runs Alembic migrations at startup,
and deploys pushes to the default branch automatically.

Configure these values in the Render service:

- `DATABASE_URL`
- `CLERK_SECRET_KEY`
- `CLERK_PUBLISHABLE_KEY`
- `CLERK_JWKS_URL`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`
- `WEB_ORIGINS=https://jobs.hemnaath.tech`

Optional features use `FIRECRAWL_API_KEY`, `THEIRSTACK_API_KEY`, and the `R2_*`
values documented in `.env.example`.

Render's free web-service plan can still spin down, but it is no longer in the
Kanban board's critical path. A cold start can affect resume or discovery tools,
not routine board loads, moves, archives, or refreshes.

## Backend → Vercel container

Create a second Vercel project from this repository with **Root Directory**
set to `apps/api`. Do not change the existing web project's root directory.

Copy the backend environment variables listed above and add:

- `DB_POOL_SIZE=2`
- `DB_MAX_OVERFLOW=3`
- `WEB_ORIGINS=https://jobs.hemnaath.tech`

The container command does not run Alembic automatically because multiple
instances can start concurrently. Apply migrations once from a trusted release
environment before pointing the frontend at the Vercel backend.

After the new backend passes `/health`, `/health/ready`, authenticated API, and
PDF smoke tests, update the web project's `API_BASE_URL` to the new backend URL
and redeploy. Keep Render available until the production smoke test passes.

Vercel containers scale to zero. In the July 2026 cutover, the first request to
a new instance took roughly 8 seconds; warm health requests were around 150 ms.
Use an always-on Render or Fly instance if eliminating every cold start matters
more than scale-to-zero cost savings.

## Web → Vercel

The Vercel project uses `infra/vercel/vercel.json` and should have:

- `API_BASE_URL=https://job-os-api.onrender.com`
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
- `CLERK_SECRET_KEY`
- `NEXT_PUBLIC_APPWRITE_ENDPOINT`
- `NEXT_PUBLIC_APPWRITE_PROJECT_ID`
- `NEXT_PUBLIC_APPWRITE_DATABASE_ID=job-os`
- `NEXT_PUBLIC_APPWRITE_APPLICATIONS_TABLE_ID=application_cards`
- `APPWRITE_API_KEY` (server-only and marked sensitive)
- `NEXT_PUBLIC_PIPELINE_BACKEND=appwrite`

The applications board uses Appwrite after the Next.js session bridge exchanges
the Clerk identity for a short-lived Appwrite token. Other routes continue
through `/api/backend/*` to FastAPI.

## Smoke test

```bash
curl https://job-os-api.onrender.com/health
# {"status":"ok","version":"0.0.1"}
```

Then open `https://jobs.hemnaath.tech`, sign in, and confirm the Applications
board loads immediately, a card move survives refresh, and resume/profile tools
still wake the Python backend when needed.
