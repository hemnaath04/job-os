# Job OS agents

Appwrite Function for slow resume operations. Browser CRUD uses TablesDB and
Storage directly; this function handles only extraction, revision, review, and
finalization and writes progress to the `agent_jobs` table.

Recommended Appwrite configuration:

- Runtime: Python 3.12
- Root directory: repository root
- Entrypoint: `apps/functions/job-os-agents/main.py`
- Build command:
  `pip install -r apps/functions/job-os-agents/requirements.txt && pip install --no-deps ./apps/api`
- Timeout: 900 seconds
- Execute access: authenticated users
- Scopes: rows read/write, files read/write
- Function ID: `job-os-agents`

Set `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, the three model variables, and
the two Manifest tier variables as secret function variables. Appwrite injects
the project ID, endpoint, and dynamic API key.

## Tests

`pyproject.toml` beside this file is a test-only environment, not part of the
deploy: `main.py` still ships as a single file per the build command above.

```
cd apps/functions/job-os-agents
uv sync
uv run pytest
```

Wired into `.github/workflows/ci.yml` as its own `functions` job, running the
same two commands above on every push and pull request, in parallel with
`api` and `web` and independent of both.
