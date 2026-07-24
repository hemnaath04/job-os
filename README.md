# job.os

Personal AI-powered job-application OS.
Tracker + resume tailoring + multi-source discovery, with a hard "no hallucination"
invariant on resume content (every bullet cites a verified profile fact).

## Layout

```
apps/
  web/        Next.js 15 (App Router, TypeScript, Tailwind, shadcn/ui)
  api/        FastAPI + async SQLAlchemy + pgvector
infra/        Render, Fly.io, and Vercel deployment configuration
```

## Stack

| Layer            | Choice                                          |
| ---------------- | ----------------------------------------------- |
| Frontend         | Next.js 15, TypeScript, Tailwind, shadcn/ui     |
| Backend          | FastAPI (Python 3.13), async SQLAlchemy 2.0     |
| Database         | Postgres 16 + pgvector (Neon)                   |
| Data fetching    | TanStack Query through an authenticated proxy   |
| LLM              | Anthropic-compatible gateway (model configurable) |
| Job discovery    | TheirStack + SimplifyJobs GitHub data           |
| Job-page import  | Firecrawl, with direct HTTP fallback             |
| Resume render    | WeasyPrint + Jinja2                              |
| Auth             | Clerk                                           |
| Blob             | Cloudflare R2 (optional)                        |
| Hosting          | Vercel (web) + Render (FastAPI)                 |

## Milestones

- **M1 (week 1)** — Replaces the spreadsheet. Add jobs from URL, Kanban + table view.
- **M2 (week 2)** — Profile KB + Reactive Resume render of master resume.
- **M3 (week 3)** — Tailoring agent with provenance guardrails.
- **M4 (week 4)** — Discovery feed (TheirStack + GitHub repos).

## Local dev

```bash
# Backend
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn job_os.main:app --reload

# Frontend
cd apps/web
pnpm install
pnpm dev
```

Neon, Clerk, and the Anthropic-compatible endpoint are the core production
services. TheirStack, Firecrawl, and R2 enable optional discovery, import, and
artifact-storage capabilities. See `.env.example`.
