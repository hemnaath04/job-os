from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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

    # Defaults to production deliberately. This used to default to "development",
    # which made the 503 fail-safe in auth.get_current_user unreachable: that guard
    # is skipped when is_dev is true, so a deployment with no APP_ENV and no Clerk
    # configuration served every route to anyone as one shared `dev-local` account,
    # and an anonymous request wrote a users row. heroku.yml declares no env at all,
    # so the default is what a fresh deploy gets. An unsafe default that has to be
    # overridden to become safe is backwards.
    app_env: str = Field(default="production")
    log_level: str = Field(default="info")

    # Second, deliberate opt-in for the anonymous dev user. APP_ENV=development on
    # its own is not enough, because that is the kind of value that gets set by
    # accident in a dashboard or inherited from a copied .env. Both signals or no
    # dev user.
    allow_anonymous_dev_user: bool = Field(default=False)

    database_url: str = Field(..., description="postgresql+asyncpg://...")
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
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
    # Manifest custom routing tiers. Fast handles short structured tasks;
    # quality is reserved for resume extraction; sonnet handles resume
    # tailoring and the independent review/verify pass.
    #
    # The fast tier does not behave like the other two, and moving a step onto
    # it is not the one-line change it looks like. Probed against the live
    # gateway: job-os-sonnet serves claude-sonnet-5 and job-os-quality serves
    # claude-opus-5 whatever model id they are handed, so the id is decoration
    # there. job-os-fast honours the id instead. A bare "auto" routes to
    # claude-opus-4-8, so sending a step there to make it cheap, without naming
    # "claude-haiku-4-5", buys the most expensive model on the gateway.
    #
    # "manifest/auto", which is what ANTHROPIC_MODEL_TAILOR is set to, is worse
    # than that: the fast tier answers 200 OK with stop_reason end_turn and the
    # string "[Manifest M302] Model \"manifest/auto\" is not available" as the
    # assistant's reply. Nothing raises. The caller sees a well-formed response
    # whose text is an error message, JSON parsing fails, and a step written to
    # fail soft returns empty as though the model had found nothing.
    manifest_tier_fast: str = "job-os-fast"
    manifest_tier_quality: str = "job-os-quality"
    manifest_tier_sonnet: str = "job-os-sonnet"

    firecrawl_api_key: str | None = None
    apify_api_token: str | None = None
    browserbase_api_key: str | None = None
    browserbase_project_id: str | None = None
    theirstack_api_key: str | None = None
    github_token: str | None = None

    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_public_base_url: str | None = None

    reactive_resume_base_url: str | None = None
    reactive_resume_api_key: str | None = None

    @field_validator("anthropic_base_url")
    @classmethod
    def _normalize_anthropic_base_url(cls, value: str | None) -> str | None:
        """Hand the Anthropic SDK an origin, not a versioned path.

        The SDK appends `/v1/messages` itself, so a base URL that already ends
        in `/v1` produces a request to `/v1/v1/messages` and every call 404s
        with "Cannot POST /v1/v1/messages". Gateways are routinely documented
        with the version included (Manifest publishes
        `https://app.manifest.build/v1`), so accept either form and normalize.
        """
        if not value:
            return None
        trimmed = value.strip().rstrip("/")
        if trimmed.endswith("/v1"):
            trimmed = trimmed[: -len("/v1")]
        return trimmed or None

    @property
    def is_dev(self) -> bool:
        """Which environment this is. Exact and case-sensitive, so neither "dev" nor
        "Development" counts."""
        return self.app_env == "development"

    @property
    def dev_user_enabled(self) -> bool:
        """Whether `get_current_user` may mint the anonymous `dev-local` user.

        Deliberately separate from `is_dev`. The other is_dev consumer
        (routers/profile.py, which gates importing a resume from an arbitrary path on
        the server's filesystem) is a different capability, and folding both behind a
        flag named after the dev user would mean enabling one silently enables the
        other.
        """
        return self.is_dev and self.allow_anonymous_dev_user


@lru_cache
def get_settings() -> Settings:
    return Settings()
