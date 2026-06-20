from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    """Find the project root by walking up from this file looking for a
    marker file. Works in both layouts:
      - Local monorepo:  job-app-manager/   (pnpm-workspace.yaml at root)
      - Docker image:    /app/              (pyproject.toml + alembic.ini)

    Looks for the monorepo marker first (since apps/api also has
    pyproject.toml + alembic.ini and we want the higher-level dir in dev).
    Falls back to the filesystem root if no marker is found — pydantic-
    settings tolerates env_file paths that don't exist."""
    here = Path(__file__).resolve()
    parents = list(here.parents)
    for parent in parents:
        if (parent / "pnpm-workspace.yaml").is_file():
            return parent
    for parent in parents:
        if (parent / "pyproject.toml").is_file() and (parent / "alembic.ini").is_file():
            return parent
    return parents[-1]


REPO_ROOT = _find_repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = Field(default="development")
    log_level: str = Field(default="info")

    database_url: str = Field(..., description="postgresql+asyncpg://...")
    redis_url: str | None = None

    clerk_secret_key: str | None = None
    clerk_publishable_key: str | None = None
    clerk_jwks_url: str | None = None

    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    # Claude is the only LLM provider — retrieval, extraction, tailoring, verification.
    # We don't use embeddings; retrieval uses Claude directly over the (small) profile.
    anthropic_model_extract: str = "manifest/auto"
    anthropic_model_tailor: str = "manifest/auto"
    anthropic_model_verify: str = "manifest/auto"

    firecrawl_api_key: str | None = None
    apify_api_token: str | None = None
    browserbase_api_key: str | None = None
    browserbase_project_id: str | None = None
    theirstack_api_key: str | None = None

    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_public_base_url: str | None = None

    reactive_resume_base_url: str | None = None
    reactive_resume_api_key: str | None = None

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
