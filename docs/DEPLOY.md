# Production deployment

Current, verified 2026-08-04. Everything below was checked against the live
platforms rather than transcribed from intent.

| piece | where | how it deploys |
| --- | --- | --- |
| Next.js web app | Vercel project `job-app-manager` → `jobs.hemnaath.tech` | automatic on push to `main` |
| FastAPI API | Heroku app `job-os` → `https://job-os-328bbf87b8ba.herokuapp.com` | **manual** `container:push`, see below |
| Application board, resumes, agent work | Appwrite project `6a6552db0034a120b320`, region `nyc` | `appwrite push functions` |
| Durable job/application data | Neon Postgres, via the API's `DATABASE_URL` | migrations run manually |

Production runs `NEXT_PUBLIC_PIPELINE_BACKEND=appwrite` and
`NEXT_PUBLIC_WORKSPACE_BACKEND=appwrite`, so resume review and finalize execute in
the **Appwrite function**, not in the FastAPI container. The container serves jobs,
applications, discovery and profile. Knowing which is which matters: the function
ships no LaTeX engine.

Not deployment targets, despite still being in the tree: `render.yaml`, `fly.toml`,
`infra/fly/api.fly.toml`. They are queued for deletion. The Vercel project
`job-os-api` is a retired half-finished deploy that currently answers 500 or times
out; ignore it.

---

## API → Heroku (manual, and the only way it ships)

**Owner: whoever merges the change.** There is no CD for the API. A merge to `main`
deploys the web app and does nothing to the API, so main and production drift
silently until someone runs this. `/health` reports the deployed commit
(`git_sha`) and the `api-health` workflow warns when it does not match main's tip.

### Prerequisites

- `heroku` CLI, logged in (`heroku auth:whoami`)
- a Docker daemon. On Apple Silicon: `colima start --cpu 4 --memory 6 --disk 30`
- **the image must be `linux/amd64`.** Heroku's container registry is x86_64-only
  (<https://devcenter.heroku.com/changelog-items/2718>), and Docker on Apple Silicon
  defaults to arm64. Omitting `--platform` produces an image Heroku rejects.

### The commands

```sh
cd apps/api

# 1. Cross-build for amd64, tagging the registry directly, and bake in the commit.
#    Context is apps/api because Heroku sets the build context to the Dockerfile's
#    own directory and cannot be configured otherwise.
docker build \
  --platform linux/amd64 \
  --build-arg GIT_SHA="$(git rev-parse HEAD)" \
  -f Dockerfile.vercel \
  -t registry.heroku.com/job-os/web \
  .

# 2. Push and release.
heroku container:login
docker push registry.heroku.com/job-os/web
heroku container:release web -a job-os
```

Expect the build to be slow on Apple Silicon: every layer runs under emulation, and
the image compiles all six LaTeX templates to warm the Tectonic cache.

`Dockerfile.vercel` is named for a platform that no longer builds it. Renaming it
means editing `heroku.yml` in the same commit; tracked in
`_artifacts/qa/followups.md`.

### Migrations

The container deliberately does **not** run Alembic on boot, because several
instances can start at once. Run it once, before releasing a migration:

```sh
heroku run -a job-os -- /app/.venv/bin/python -m alembic upgrade head
```

### Verify, every time

```sh
H=https://job-os-328bbf87b8ba.herokuapp.com

heroku releases -a job-os -n 3          # the release number must have incremented
curl -s $H/health                       # {"status":"ok","version":"...","git_sha":"<the commit you built>"}
curl -s -o /dev/null -w '%{http_code}\n' $H/health/ready              # 200
curl -s -o /dev/null -w '%{http_code}\n' $H/api/v1/applications       # 401
curl -s -o /dev/null -w '%{http_code}\n' $H/docs                      # 404 in production
```

`401` on an authenticated route is the check that matters: it proves Clerk
verification is configured. A `200` there would mean the API is serving everyone as
one shared account, and a `503` would mean auth configuration is missing entirely.
Both are stop-and-investigate.

`/docs` returning `404` is correct in production. It returned `200` until
2026-08-04 and served the full schema anonymously.

### Config vars

Set in Heroku, not in this repo (`heroku config -a job-os` to list names):
`DATABASE_URL`, `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`,
`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `SENTRY_DSN`, `FIRECRAWL_API_KEY`,
`WEB_ORIGINS`, `APP_ENV`.

`APP_ENV=production` is set, and since 2026-08-04 that is also the code default, so
omitting it is safe. **Never set `ALLOW_ANONYMOUS_DEV_USER` on a deployed target.**
It is not a convenience flag there; with Clerk unconfigured it serves every request
as one shared account.

`RENDER_ENGINE=typst` is baked into the image rather than set as a config var.

---

## Web → Vercel (automatic)

Project `job-app-manager`, deploys on push to `main`. Environment variables live in
the Vercel dashboard; `vercel env ls production` lists the names.

`API_BASE_URL` points at the Heroku origin. The browser never calls it directly —
`apps/web/next.config.mjs` has no rewrites, and `/api/backend/*` is handled by an
auth-injecting Route Handler that forwards server-side. `NEXT_PUBLIC_API_BASE_URL`
appears in `.env.example` and is read by nothing.

## Appwrite function

```sh
appwrite push functions
```

Deploys `apps/functions/job-os-agents`. Its environment is configured in the
Appwrite console, under names that differ from the `NEXT_PUBLIC_APPWRITE_*` ones in
`.env.example` — see the Phase 0 finding in `_artifacts/qa/findings.md`.

## Smoke test after any deploy

```sh
curl -s https://job-os-328bbf87b8ba.herokuapp.com/health
```

Then open `https://jobs.hemnaath.tech`, sign in, and confirm the Applications board
loads, a card move survives a refresh, and a resume opens.
