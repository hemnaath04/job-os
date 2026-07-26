# Appwrite revamp: safe pipeline cutover

This is a staged migration. Neon is not deleted or overwritten. The
applications board can move to Appwrite independently while resume generation,
profile data, and AI jobs continue to use the FastAPI service.

## What changes first

- Application cards load from Appwrite TablesDB.
- Clerk remains the sign-in screen. A short-lived server bridge creates the
  matching Appwrite session; the Appwrite API key never reaches the browser.
- Kanban moves update optimistically, so the card moves before either backend
  responds.
- While Appwrite is primary, application edits are also written to Neon. Neon
  remains the rollback copy.
- Basic JD parsing and smart search use the `job-os-fast` Manifest tier.
- Resume extraction and tailoring use the `job-os-quality` Manifest tier.
- Python owns schema bootstrap, export, import, verification, and AI work.
- Resume tailoring is a LangGraph workflow: draft, deterministic Python ATS
  scoring, conditional refinement, then final no-hallucination assembly.

LangGraph is intentionally not placed in the Kanban request path. Moving a
card is deterministic CRUD; wrapping it in an agent would add latency without
adding intelligence.

## Project setup

The production `job-os` Appwrite Education project is registered for
`jobs.hemnaath.tech`. Its server key expires yearly and is restricted to the
user, database, table, column, index, and row scopes used by the session bridge
and migration scripts. Set these environment variables locally and in Vercel:

```dotenv
NEXT_PUBLIC_PIPELINE_BACKEND=legacy
NEXT_PUBLIC_APPWRITE_ENDPOINT=https://cloud.appwrite.io/v1
NEXT_PUBLIC_APPWRITE_PROJECT_ID=...
NEXT_PUBLIC_APPWRITE_DATABASE_ID=job-os
NEXT_PUBLIC_APPWRITE_APPLICATIONS_TABLE_ID=application_cards
APPWRITE_API_KEY=...
```

Do not paste or commit `APPWRITE_API_KEY`.

## Bootstrap, copy, and verify

From the repository root, bootstrap the staging schema using the Python
backend:

```bash
pnpm appwrite:bootstrap
```

Export a read-only snapshot from Neon:

```bash
cd apps/api
uv run python -m job_os.scripts.export_appwrite_snapshot \
  /tmp/job-os-appwrite-snapshot.json
cd ../..
```

Import and verify it with Python:

```bash
pnpm appwrite:import /tmp/job-os-appwrite-snapshot.json
```

The importer preserves every application UUID, timestamp, nested job/company
snapshot, status, and archive state. It then reads the Appwrite rows back and
compares their content hashes. A mismatch exits with an error.

## Verified migration and cutover

On July 25, 2026, the Python migration exported 18 production applications from
Neon, imported them into Appwrite, and verified every card by ID, status,
archive state, and SHA-256 hash of its complete nested snapshot.

Only after the importer reports success:

```dotenv
NEXT_PUBLIC_PIPELINE_BACKEND=appwrite
```

Deploy and test:

- board count matches production;
- every status column matches;
- drag, archive, and undo work;
- refreshing keeps the change;
- a newly added job appears on the board;
- resume/profile screens still work.

## Rollback

Set `NEXT_PUBLIC_PIPELINE_BACKEND=legacy` and redeploy. The app immediately reads
Neon again. Do not delete the Appwrite project or Neon database during the
revamp.
