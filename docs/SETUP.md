# Cloud setup checklist

Run through these once. All accounts have free tiers; total cost at personal
volume should be < $10/mo until you start tailoring resumes heavily.

## 1. Postgres (Neon)

1. https://console.neon.tech → create project `job-os`
2. Region: `aws-us-east-1` (matches Vercel + Fly default)
3. Once provisioned, open the SQL editor and run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS pgcrypto;
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   (Alembic will also create them, but doing it once now confirms your tier
   supports `vector`.)
4. Copy the **pooled** connection string (with `-pooler`) from the dashboard.
5. Convert to asyncpg form and put in `.env`:
   ```
   # postgres://user:pass@host/db   →
   DATABASE_URL=postgresql+asyncpg://user:pass@host/db?ssl=true
   ```

## 2. Redis (Upstash)

1. https://console.upstash.com → create Redis database (free tier, region as
   close to Fly as possible).
2. Copy the `rediss://...` connection string into `REDIS_URL`.

## 3. Clerk

1. https://dashboard.clerk.com → create application `job.os`.
2. Sign-in methods: enable Email + Google (you'll thank yourself later when
   you integrate Gmail / Calendar).
3. From "API Keys" copy:
   - `CLERK_PUBLISHABLE_KEY` (also `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`)
   - `CLERK_SECRET_KEY`
4. From "JWT Templates" → copy the JWKS URL into `CLERK_JWKS_URL`.

## 4. Anthropic + OpenAI

1. https://console.anthropic.com → create API key → `ANTHROPIC_API_KEY`.
   - If you want to route via your Zavora gateway instead, set
     `ANTHROPIC_BASE_URL` and reuse the gateway key.
2. https://platform.openai.com/api-keys → create key → `OPENAI_API_KEY`
   (used only for embeddings).

## 5. Firecrawl

1. https://www.firecrawl.dev → sign up, copy `FIRECRAWL_API_KEY`.
2. Free tier gives ~500 scrapes/month — plenty for personal use.

## 6. Cloudflare R2 (PDF/DOCX storage)

1. https://dash.cloudflare.com → R2 → create bucket `job-os-artifacts`.
2. Manage R2 API tokens → create token with **Object Read+Write** scope on
   that bucket. Copy: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_ACCOUNT_ID`.
3. Optional: enable public R2.dev URL (for shareable preview links).

## 7. Reactive Resume (self-host for PDF/DOCX rendering)

Defer until **M2** — for M1 we don't render resumes yet.

When you're ready: deploy the official Docker image on Fly.io
(https://docs.rxresu.me/engineering/self-hosting). Point `REACTIVE_RESUME_BASE_URL`
at it.

## 8. TheirStack

You already have an API key in your existing MCP setup. Copy it into
`THEIRSTACK_API_KEY`.

---

## Local first-run

```bash
cd ~/Documents/projects/job-app-manager
cp .env.example .env
# fill DATABASE_URL, CLERK_*, ANTHROPIC_API_KEY, FIRECRAWL_API_KEY at minimum

# Backend
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn job_os.main:app --reload     # → http://localhost:8000

# Frontend (separate terminal)
cd ../web
pnpm install
pnpm dev                                     # → http://localhost:3000
```

The first time you open http://localhost:3000/applications:
- Clerk's middleware will redirect you to sign-in
- After sign-in you'll see an empty Kanban
- Click **Add job** and paste any job URL (Greenhouse / Lever / Ashby /
  company career page) — Firecrawl + Haiku 4.5 will parse it and add a
  wishlist card

If you don't set `CLERK_SECRET_KEY`, the API falls back to a single hardcoded
`dev@local` user — useful for offline iteration but won't proxy through the
web app's auth middleware.
