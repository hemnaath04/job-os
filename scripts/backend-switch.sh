#!/usr/bin/env bash
#
# Switch the web app between its two backends and redeploy.
#
#   ./scripts/backend-switch.sh legacy     # Postgres, via the FastAPI on Heroku
#   ./scripts/backend-switch.sh appwrite   # Appwrite, direct from the browser
#   ./scripts/backend-switch.sh status     # what is live right now
#
# WHY THIS EXISTS
#   On 2026-08-31 an ingest job exhausted the Appwrite database-read quota for
#   the whole billing cycle (Aug 25 - Sep 24). Appwrite answers every read with
#   402 `limit_databases_reads_exceeded`, including tables the crawl never
#   touched, so the applications board rendered empty even though Postgres held
#   every row. Waiting 23 days for the reset was not acceptable, and flipping
#   these two variables was: the code already carries both paths.
#
#   `src/lib/appwrite/config.ts` reads exactly one string per backend and
#   treats anything that is not "appwrite" as "legacy", so "legacy" is a real
#   value rather than an unset variable. Unsetting would work too; naming it
#   makes `vercel env ls` say which mode is intended rather than leaving a
#   reader to infer it from an absence.
#
# WHAT MOVES
#   PIPELINE  applications board, calendar, status changes.  Postgres holds all
#             of it, and `createApplication` has always written Postgres first
#             and mirrored to Appwrite second, so nothing is stranded.
#   WORKSPACE resumes, versions, tailoring. Metadata is in Postgres, but rendered
#             FILES for Appwrite-era versions live in Appwrite Storage, which is
#             a separate quota from the database reads that ran out. Downloads
#             are the thing to check first after switching this one.
set -euo pipefail

PROJECT="job-app-manager"
MODE="${1:-status}"

read_current() {
  # `vercel env pull` writes a file and prints its own progress to stdout, so it
  # cannot be piped; it gets a scratch file that is removed on the way out.
  local tmp
  tmp="$(mktemp)"; trap 'rm -f "$tmp"' RETURN
  vercel env pull "$tmp" --environment=production --project "$PROJECT" --yes >/dev/null 2>&1 || true
  local pipeline workspace
  pipeline="$(grep -m1 NEXT_PUBLIC_PIPELINE_BACKEND "$tmp"  | cut -d= -f2- | tr -d '"')"
  workspace="$(grep -m1 NEXT_PUBLIC_WORKSPACE_BACKEND "$tmp" | cut -d= -f2- | tr -d '"')"
  echo "  pipeline : ${pipeline:-<unset, behaves as legacy>}"
  echo "  workspace: ${workspace:-<unset, behaves as legacy>}"
}

case "$MODE" in
  status)
    echo "live production backends:"; read_current; exit 0 ;;
  legacy|appwrite) ;;
  *) echo "usage: $0 [legacy|appwrite|status]" >&2; exit 2 ;;
esac

for VAR in NEXT_PUBLIC_PIPELINE_BACKEND NEXT_PUBLIC_WORKSPACE_BACKEND; do
  # `env add` will not overwrite, so the old value has to go first. A removal
  # that fails because the variable was already absent is fine; a redeploy
  # would just fall back to "legacy", which is the safe half of this switch.
  vercel env rm "$VAR" production --project "$PROJECT" --yes >/dev/null 2>&1 || true
  printf '%s' "$MODE" | vercel env add "$VAR" production --project "$PROJECT" >/dev/null 2>&1
  echo "  set $VAR=$MODE"
done

# Env changes only reach the browser through a build: NEXT_PUBLIC_* values are
# inlined at compile time, so redeploying is part of the switch, not a follow-up.
echo "redeploying (NEXT_PUBLIC_* are baked in at build time)..."
vercel deploy --prod --project "$PROJECT" --yes 2>&1 | tail -3
