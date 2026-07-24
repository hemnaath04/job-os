# Production deployment

The current production setup has two independently deployed services:

- Vercel serves the Next.js web app at `jobs.hemnaath.tech`.
- Render serves the FastAPI backend at `job-os-api.onrender.com`.

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

Render's free web-service plan spins down after 15 minutes without inbound
traffic. `.github/workflows/keep-warm.yml` calls `/health` every five minutes
to reduce cold starts. For dependable production latency, change the Render
service itself to a paid instance that does not spin down.

## Web → Vercel

The Vercel project uses `infra/vercel/vercel.json` and should have:

- `API_BASE_URL=https://job-os-api.onrender.com`
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
- `CLERK_SECRET_KEY`

The browser calls `/api/backend/*` on the web origin. The Next.js route handler
adds the signed-in Clerk token and forwards requests to FastAPI.

## Smoke test

```bash
curl https://job-os-api.onrender.com/health
# {"status":"ok","version":"0.0.1"}
```

Then open `https://jobs.hemnaath.tech`, sign in, and confirm that Dashboard and
Applications load without a cold-start wait.
