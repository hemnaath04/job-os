# job.os

A personal AI job-search platform, live at [jobs.hemnaath.tech](https://jobs.hemnaath.tech).

It does three things:

1. **Tracks applications.** Add a role from its URL, then move it through a Kanban
   board or work it in a table.
2. **Tailors resumes.** An agent rewrites a master resume against one specific
   posting, grounded strictly in facts the resume or profile already verifies.
3. **Finds roles.** Discovery pulls postings from TheirStack and the SimplifyJobs
   GitHub lists. A single job page imports through Firecrawl, with a direct HTTP
   fallback.

The tracker is a tracker. The tailoring engine is the part worth reading.

## The tailoring engine

Most AI resume tools will happily invent a metric, a technology, or a whole
domain of experience because a job description asked for it. This one cannot.
The invariant is that every claim on the page has to trace back to something
already verified, and the enforcement is code rather than a polite instruction
in a prompt.

### What the agent is allowed to see

Alongside the posting, the drafting model gets three sources of truth about the
candidate and nothing else: the current JSON Resume, verified profile facts, and
the current README text fetched live from GitHub for projects the resume
includes. JSON Resume is the canonical, editable document. LaTeX and PDF are
generated artifacts.

Before the model sees anything, duplicate facts are merged, so re-importing a
resume cannot mint a second copy of the same job and produce a role with seven
near-identical bullets.

### Honesty guards

These run deterministically, after generation, on the model's output:

- **Unverified numbers are stripped.** A figure no verified fact supports never
  reaches the page, whatever the model wrote.
- **Unevidenced domains are flagged.** If the summary claims a subject-matter
  domain that nothing else on the page can back, it is reported as an overclaim
  rather than shipped.
- **New entities are rejected.** Employers, dates, coursework, metrics,
  technologies, credentials, and outcomes are refused unless they already exist
  in the resume or in a verified fact.
- **Bullets can be reshaped, not inflated.** Growth past a fixed cap is how
  job-description padding gets in, so it is measured and blocked.
- **Unsupported requirements become questions.** A requirement the profile cannot
  support turns into a gap question or a suggestion. It never turns into a
  bullet.
- **Positioning is fixed.** The resume is written for AI/ML, backend and systems,
  LLM agents, and test automation. Some skills are held back from the printed
  page even when the vault records them, because listing a skill you do not have
  is the one kind of lie an interview exposes immediately.

### The review score

Review is deterministic, not vibes. A reviewing model and a set of rule-based
checks both emit a list of issues, each with a severity. The 0 to 100 score is
then derived from that weighted issue list, so the same issues always produce
the same number instead of the score whiplash a free-form model rating gave.

Finalizing a version requires all of:

- a score of at least 90,
- no issue at `blocking` severity,
- a PDF that is exactly one page,
- a PDF that contains selectable text,
- required contact fields and professional experience present.

A failed review stays attached to the version, so the next revision starts from
the specific complaint. Drafts stay previewable, but download and export are
available only once a version is finalized.

### Job match and keyword coverage

Requirements are parsed out of the posting and matched against the candidate's
own words. Two details matter:

- Only the **values** in the JSON Resume are scored, never the schema key names.
  Scoring the serialized document meant a posting asking for "summary",
  "location" or "keywords" matched the schema and inflated coverage for free.
- Matching is on word boundaries, hand-rolled rather than `\b`, because the terms
  include C++, CI/CD and .NET. A plain substring test credited a resume listing
  MongoDB with knowing Go.

Coverage is reported as matched over total, with the missing terms listed so the
gap is visible instead of quietly averaged away.

## Rendering

The PDF is real LaTeX, compiled by [Tectonic](https://tectonic-typesetting.github.io/)
inside the API container. Six templates ship with the app:

| Template          | Layout        | Upstream author        | Licence                    |
| ----------------- | ------------- | ---------------------- | -------------------------- |
| Jake's Resume     | single column | Jake Gutierrez         | MIT                        |
| sb2nov            | single column | Sourabh Bajaj          | MIT                        |
| Awesome-CV        | single column | Claud D. Park           | LPPL-1.3c                  |
| AltaCV            | two column    | LianTze Lim            | LPPL-1.3 or later          |
| ModernCV (banking) | single column | Xavier Danaux          | LPPL-1.3c                  |
| Deedy             | two column    | Debarghya Das          | Apache-2.0, fonts SIL OFL  |

Each is vendored with its upstream licence and an `ATTRIBUTION.md` recording
every change. Nothing is fetched at render time. Jake's is the default: single
column, no icon glyphs, most likely to survive a parser. The picker states
plainly which layouts are two-column, because a two-column resume really does
confuse some applicant tracking systems and hiding that costs somebody an
interview.

Implementation notes that are load-bearing:

- Every value is LaTeX-escaped exactly once, in `build_render_model`, so a
  template only ever receives escaped strings.
- Compiles run in a scratch directory with `--untrusted` and a scrubbed
  environment. A stored template is untrusted input this process is about to
  execute.
- The image compiles all six at build time. That bakes Tectonic's package cache
  in, fails the build if a template stops compiling, and lets requests render
  offline with `--only-cached`.
- A custom template can be uploaded. A `.tex` keeps the design exactly; a `.pdf`
  is rebuilt as LaTeX and comes close rather than matching. Either way it has to
  compile against sample data before it is stored, and a failure goes back to the
  model with the compiler's own log, up to four attempts.
- Template preview cards show the PDF produced from obviously invented sample
  data, never the user's own history.
- Every render is audited for what its **text layer** hands a parser, which is
  not always what the page shows. The primary check is engine-agnostic: what
  fraction of the resume's own words can still be found, as whole words, in the
  extracted text. AltaCV under Tectonic scores 26% against 98% for the same
  resume through Typst. Three narrower patterns corroborate it, because a clean
  render only scores about 85% anyway once a template legitimately omits a
  section: leaked LaTeX macros (`\faGlobe`), small caps decomposing so "Computer
  Science" arrives as `COMPUTERSCiENCE`, and lost word spacing. All of it looks
  perfect on screen. See `apps/api/src/job_os/services/pdf_text_audit.py`.

## Architecture

```
apps/
  web/                    Next.js App Router, TypeScript, Tailwind, shadcn/ui
  api/                    FastAPI container, Tectonic, async SQLAlchemy, pgvector
  functions/job-os-agents Appwrite Python function, the async agent worker
infra/                    Deployment configuration
docs/                     Engine, cutover, deploy and setup notes
```

Work is split by latency rather than by layer.

**Appwrite TablesDB and Storage** hold the interactive workspace: application
cards, resumes, immutable resume versions, revision messages, verified profile
facts, fact bullets, templates, and agent job rows. The browser reads and writes
these directly through the authenticated Appwrite Web SDK, so a board load, a
drag, or an edit never waits on a Python cold start.

**The FastAPI container** owns the heavy and stateful work: LaTeX rendering,
job and company records, saved searches, discovery, and vector search over
Postgres with pgvector. It is also the durable store the Appwrite workspace was
migrated from.

**The Appwrite function** runs expensive agent work asynchronously: profile
extraction, resume revision, review, finalization, tailoring, job parsing, and
discovery. The UI creates a queued job row and polls its status rather than
holding an HTTP request open. The function has no LaTeX engine, so it returns a
review without a page count and the browser fetches the PDF from the container.

The tailoring agent itself, `run_tailor`, takes no database handle. Both the
FastAPI path and the Appwrite function drive the exact same draft, score, refine
graph, which is the only reason two runtimes can be trusted to produce the same
resume.

**Clerk** handles auth, bridged to a short-lived Appwrite session for the direct
browser reads.

| Layer         | Choice                                                    |
| ------------- | --------------------------------------------------------- |
| Web           | Next.js App Router, TypeScript, Tailwind, shadcn/ui        |
| API           | FastAPI, LangGraph, async SQLAlchemy, Tectonic             |
| Workspace     | Appwrite TablesDB, Storage, Python Functions               |
| Jobs and vectors | Postgres with pgvector                                  |
| Auth          | Clerk                                                      |
| Models        | Claude, routed through a Manifest gateway                  |
| Discovery     | TheirStack, SimplifyJobs GitHub data                       |
| Job import    | Firecrawl, with a direct HTTP fallback                     |
| Hosting       | Vercel, Appwrite Cloud                                     |

## Running it locally

```bash
# API
cd apps/api
uv sync
uv run alembic upgrade head
uv run uvicorn job_os.main:app --reload      # docs at localhost:8000/docs

# Web
cd apps/web
pnpm install
pnpm dev                                     # localhost:3000
```

Copy `.env.example` to `.env` and fill it in first. `DATABASE_URL`, the `CLERK_*`
values and `ANTHROPIC_API_KEY` are the minimum; Firecrawl, TheirStack and R2 are
optional and their features degrade rather than crash without them.

PDF rendering needs `tectonic` on `PATH`. The container installs a pinned release
and pre-warms its package cache, see `apps/api/Dockerfile.vercel`.

First-time Appwrite setup, from the repo root:

```bash
pnpm appwrite:bootstrap           # create tables, buckets and indexes
pnpm appwrite:migrate-workspace   # copy an existing Postgres workspace across
```

## Docs

- `docs/resume-engine.md` walks the resume workflow, evidence rules and quality
  gate end to end.
- `docs/appwrite-backend-cutover.md` covers the split above and the migration
  order behind it.
- `docs/DEPLOY.md` and `docs/SETUP.md` cover production and first-run
  configuration.

## Acknowledgements

[**pdf-inspector**](https://github.com/firecrawl/pdf-inspector) by Firecrawl
(MIT) reads the text layer of every rendered resume. It classifies the PDF as
text-based or scanned and extracts the text in reading order, in single-digit
milliseconds, which is what makes it cheap enough to run on the request path
rather than in a batch job somewhere.

To be precise about the division of labour, since it affects what the credit is
for: the library supplies the classification and the extracted text. The
coverage measurement and the patterns that decide whether that text would
survive an applicant tracking system are ours, in `pdf_text_audit.py`. The
library's own `has_encoding_issues` flag does not fire on the LaTeX defects
described under [Rendering](#rendering); it is not tuned for them. What it gives
us is a trustworthy view of the string the parser actually gets, which is the
part that used to be guesswork.

The six resume templates are vendored from their upstream authors under their
own licences, listed in the table under [Rendering](#rendering), each with an
`ATTRIBUTION.md` recording every change. Rendering itself is
[Tectonic](https://tectonic-typesetting.github.io/) and
[Typst](https://typst.app/).

