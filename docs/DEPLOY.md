# Deploy M1

Two pieces: Vercel for the web app, Fly.io for the FastAPI backend.

## 1. Backend → Fly.io

```bash
# one-time install
brew install flyctl
flyctl auth login

# from repo root
flyctl launch --copy-config --config infra/fly/api.fly.toml --no-deploy

# set secrets (these never go in the repo)
flyctl secrets set \
  DATABASE_URL='postgresql+asyncpg://...neon...?ssl=true' \
  REDIS_URL='rediss://...' \
  ANTHROPIC_API_KEY=sk-ant-... \
  OPENAI_API_KEY=sk-... \
  CLERK_SECRET_KEY=sk_live_... \
  CLERK_JWKS_URL='https://...clerk.accounts.dev/.well-known/jwks.json' \
  FIRECRAWL_API_KEY=fc-... \
  R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=job-os-artifacts \
  -a job-os-api

flyctl deploy --config infra/fly/api.fly.toml
flyctl status -a job-os-api    # confirm machine running
flyctl logs -a job-os-api      # tail
```

The CMD runs `alembic upgrade head` on startup, so the first deploy migrates Neon.

## 2. Web → Vercel

```bash
pnpm dlx vercel link             # link this directory to a new Vercel project
pnpm dlx vercel env add NEXT_PUBLIC_API_BASE_URL production
  # → https://job-os-api.fly.dev
pnpm dlx vercel env add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY production
pnpm dlx vercel env add CLERK_SECRET_KEY production

pnpm dlx vercel --prod
```

Vercel auto-detects the Next.js app inside the monorepo via `vercel.json`.

## 3. Smoke test

```bash
curl https://job-os-api.fly.dev/health
# {"status":"ok","version":"0.0.1"}

# Open the deployed web app, sign in via Clerk, paste a job URL into "Add job".
# Card should land in the Wishlist column within ~10s.
```
