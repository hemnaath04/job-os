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
RUFF_CEILING=14
MYPY_CEILING=45

cd "$(dirname "$0")/../apps/api" || exit 1

ruff_out=$(uv run ruff check src tests 2>&1 || true)
ruff_count=$(printf '%s' "$ruff_out" | sed -nE 's/^Found ([0-9]+) error.*/\1/p' | tail -1)
ruff_count=${ruff_count:-0}

mypy_out=$(uv run mypy src 2>&1 || true)
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
