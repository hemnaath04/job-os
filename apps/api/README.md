# job-os-api

FastAPI backend.

```bash
uv sync
cp ../../.env.example ../../.env
# fill in DATABASE_URL etc.

uv run alembic upgrade head
uv run uvicorn job_os.main:app --reload
```

OpenAPI docs at http://localhost:8000/docs.
