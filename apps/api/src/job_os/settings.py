from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


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
    anthropic_model_extract: str = "claude-opus-4-8"
    anthropic_model_tailor: str = "claude-opus-4-8"
    anthropic_model_verify: str = "claude-opus-4-8"

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
