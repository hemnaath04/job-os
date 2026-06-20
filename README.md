# job.os

Personal AI-powered job-application OS.
Tracker + resume tailoring + multi-source discovery, with a hard "no hallucination"
invariant on resume content (every bullet cites a verified profile fact).

## Layout

```
apps/
  web/        Next.js 15 (App Router, TypeScript, Tailwind, shadcn/ui)
  api/        FastAPI + SQLAlchemy + LangGraph + pgvector
packages/
  types/      OpenAPI-generated TS types shared by the web app
  ui-tokens/  Tailwind preset for the dark-glass / violet design system
infra/        Fly.io + Vercel + Terraform stubs
```

## Stack

| Layer            | Choice                                          |
| ---------------- | ----------------------------------------------- |
| Frontend         | Next.js 15, TypeScript, Tailwind, shadcn/ui     |
| Backend          | FastAPI (Python 3.13), async SQLAlchemy 2.0     |
| Database         | Postgres 16 + pgvector (Neon)                   |
| Cache / broker   | Redis (Upstash)                                 |
| Agents           | LangGraph + PydanticAI                          |
| LLM (tailor)     | Anthropic Claude Opus 4.8                       |
| LLM (extract)    | Anthropic Claude Haiku 4.5                      |
| Embeddings       | OpenAI `text-embedding-3-large`                 |
| Job discovery    | TheirStack → GitHub repos → Apify/Firecrawl     |
| Resume render    | Reactive Resume (self-hosted)                   |
| Auth             | Clerk                                           |
| Blob             | Cloudflare R2                                   |
| Hosting          | Vercel (web) + Fly.io (api, workers, RR)        |

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

Cloud services (Neon, Upstash, R2, Clerk, Anthropic, OpenAI, Firecrawl) are
required — see `.env.example`.
