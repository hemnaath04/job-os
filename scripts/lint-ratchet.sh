#!/usr/bin/env bash
# Lint/type ratchet: fail CI only when the violation count goes UP.
#
# ruff and mypy have been configured strictly in apps/api/pyproject.toml
# (select = [E,F,I,B,UP,N,ASYNC,S,C4,RET,SIM]; strict = true) since early in the
# project and have never been run by any automation, so there is a standing
# backlog. Blocking every push on a clean run would mean either fixing 70
# findings in one unreviewable commit or turning the checks off again.
#
# So: the counts below are a ceiling, not a target. CI fails if a change adds a
# violation. Lowering a number here is the only way it moves, and it can only
# move down -- that is the ratchet.
#
# When you fix violations, run this script, take the numbers it prints, and put
# them in the two variables. A pull request that lowers a ceiling is a good
# pull request.
set -uo pipefail

# Baseline recorded 2026-08-17, after clearing the backlog accumulated during
# Phase C's Appwrite dual-write work (fix/phase-b-ci's 2026-08-04 numbers were
# 24/46; this session's unpushed commits had drifted to 29/60 before any of it
# had ever actually run through CI).
#
# Reset 2026-08-24 to the real count: the api job's pytest step has failed
# outright on every CI run since (missing APPWRITE_API_KEY, fixed separately),
# so this script never actually ran in CI and both backlogs grew unseen across
# many parallel branches before anyone could catch a single violation at the
# old ceilings. Those were stale numbers this ratchet had no chance to
# enforce, not real targets abandoned on purpose. Numbers taken from a clean
# checkout of the actual commit, not this (or any) shared working tree: with
# several other sessions actively editing files here, a check run against the
# dirty tree reads a different, wrong count depending on the instant it runs,
# which is what produced the first, incorrect version of this reset.
#
# Lowered 2026-08-30 from 30/69 by the description-hydration branch. Not a
# cleanup pass: extracting `search_text_for` out of `upsert.to_row` (so the new
# hydration pass could not drift from the write path) happened to take one
# over-long line and two `object`-typed dict lookups with it. The new module
# and its tests add none of their own.
#
# Lowered 2026-08-31 from 29/67 by feat/postings-back-to-postgres, for the same
# kind of reason: nothing was cleaned up deliberately. Moving `job_postings`
# back to Postgres deleted `tests/_fake_appwrite.py` and
# `tests/test_appwrite_job_postings.py`, and Postgres does with typed columns
# and generated expressions what the Appwrite path had to do with
# `dict[str, object]` lookups and hand-built filter strings. The five ruff and
# three mypy findings that went with them were never fixed, they stopped
# existing.
RUFF_CEILING=24
MYPY_CEILING=64

cd "$(dirname "$0")/../apps/api" || exit 1

# --color=never / --no-color-output: without this, mypy colors its summary
# line by default in a local terminal (CI's non-interactive runner does not),
# which prepends ANSI escapes before "Found" and silently breaks the `^Found`
# anchor below -- ruff_count/mypy_count then default to 0 via `:-0`, and this
# script reports a clean run no matter how many real errors exist. That is
# exactly the kind of local-passes-when-CI-would-fail gap this script exists
# to prevent, so it needs to not have one itself.
ruff_out=$(uv run ruff check --color=never src tests 2>&1 || true)
ruff_count=$(printf '%s' "$ruff_out" | sed -nE 's/^Found ([0-9]+) error.*/\1/p' | tail -1)
ruff_count=${ruff_count:-0}

mypy_out=$(uv run mypy --no-color-output src 2>&1 || true)
mypy_count=$(printf '%s' "$mypy_out" | sed -nE 's/^Found ([0-9]+) error.*/\1/p' | tail -1)
mypy_count=${mypy_count:-0}

echo "ruff : $ruff_count violations (ceiling $RUFF_CEILING)"
echo "mypy : $mypy_count errors     (ceiling $MYPY_CEILING)"

status=0

if [ "$ruff_count" -gt "$RUFF_CEILING" ]; then
  echo "::error::ruff went UP: $ruff_count > ceiling $RUFF_CEILING"
  printf '%s\n' "$ruff_out" | tail -40
  status=1
elif [ "$ruff_count" -lt "$RUFF_CEILING" ]; then
  echo "::notice::ruff improved to $ruff_count. Lower RUFF_CEILING in scripts/lint-ratchet.sh to lock it in."
fi

if [ "$mypy_count" -gt "$MYPY_CEILING" ]; then
  echo "::error::mypy went UP: $mypy_count > ceiling $MYPY_CEILING"
  printf '%s\n' "$mypy_out" | tail -40
  status=1
elif [ "$mypy_count" -lt "$MYPY_CEILING" ]; then
  echo "::notice::mypy improved to $mypy_count. Lower MYPY_CEILING in scripts/lint-ratchet.sh to lock it in."
fi

exit $status
