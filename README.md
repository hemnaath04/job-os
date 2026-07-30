# job.os

Personal AI-powered job-application OS. The interactive application pipeline is
served from Appwrite for instant board reads and writes, while Python agents
handle discovery, profile extraction, and resume tailoring with a hard
"no hallucination" invariant.

The Resume Studio treats JSON Resume as the canonical, editable source. Manual
edits create immutable child versions, while AI chat produces a reviewable
proposal before it can be applied. Generated PDFs are stored with each version,
and only versions approved by a separate quality model plus deterministic PDF
checks can be finalized and downloaded.

## Layout

```
apps/
  web/        Next.js 15 (App Router, TypeScript, Tailwind, shadcn/ui)
  api/        Python, FastAPI, LangGraph, async SQLAlchemy, pgvector
infra/        Vercel web + Render agent-service deployment configuration
```

Appwrite TablesDB is the low-latency application pipeline. Neon Postgres
remains the rollback store and continues to support profile, resume, and vector
data used by the Python service.

## Stack

| Layer            | Choice                                          |
| ---------------- | ----------------------------------------------- |
| Frontend         | Next.js 15, TypeScript, Tailwind, shadcn/ui     |
| Backend          | FastAPI (Python), LangGraph, async SQLAlchemy    |
| Data             | Appwrite TablesDB + Neon Postgres/pgvector      |
| Data fetching    | Appwrite Web SDK + optimistic TanStack Query    |
| LLM routing      | Manifest: Haiku fast tier + quality resume tier |
| Job discovery    | TheirStack + SimplifyJobs GitHub data           |
| Job-page import  | Firecrawl, with direct HTTP fallback             |
| Resume engine    | JSON Resume, LangGraph, Claude review, GitHub evidence |
| Resume render    | Real LaTeX, compiled by Tectonic; six vendored templates |
| Auth             | Clerk                                           |
| Blob             | Cloudflare R2 (optional)                        |
| Hosting          | Vercel (web) + Appwrite + Render (Python agents) |

## Milestones

- **M1 (week 1)** — Replaces the spreadsheet. Add jobs from URL, Kanban + table view.
- **M2 (week 2)** — Profile KB + Reactive Resume render of master resume.
- **M3 (week 3)** — Tailoring agent with provenance guardrails.
- **M4 (week 4)** — Discovery feed (TheirStack + GitHub repos).
- **Resume Studio** — resume-library import, structured editing, conversational
  revisions, GitHub README verification, immutable history, and final QA.

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

The application board uses Appwrite directly through a short-lived
Clerk-to-Appwrite session bridge. Neon remains a rollback copy during the
cutover. Resume/profile screens and AI workloads stay on the Python service;
short structured tasks use the fast Manifest route, while resume extraction
and tailoring use the quality route. See `docs/appwrite-revamp.md`.

## Resume quality gate

1. Upload a master PDF, DOCX, or JSON Resume.
2. Edit fields directly or ask the resume chat to revise a specific section.
3. The agent may use only the current resume, verified profile facts, and
   current README evidence from included GitHub projects.
4. A separate quality model reviews the result after generation.
5. Deterministic checks require exactly one page and selectable PDF text.
6. A score of 90 or higher with no blocking issues is required to finalize.

All imports, edits, reviews, chat messages, PDFs, and final versions are stored
in Postgres. The original master is never overwritten. Archive actions hide
role variants and versions without erasing their stored history, and the only
remaining master version cannot be archived. See `docs/resume-engine.md`.
