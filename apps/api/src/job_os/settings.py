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
    #: The `iss` every accepted Clerk session token must carry.
    #:
    #: Not optional, and not `None`-able: PyJWT skips issuer validation entirely
    #: when the issuer it is handed is `None`, so an unset value would silently
    #: turn the check off rather than fail closed. Overriding it via CLERK_ISSUER
    #: is how a different Clerk instance is pointed at, not by clearing it.
    #:
    #: `azp` is deliberately NOT validated alongside this. The MCP OAuth clients
    #: added in #75/#78 present varied `azp` values, so pinning one client id
    #: would lock them out; the issuer is the part that is stable across them.
    clerk_issuer: str = "https://clerk.jobs.hemnaath.tech"

    # ---- Language models -----------------------------------------------------
    #
    # OpenAI directly, paid from the account's API credits, plus OpenRouter's
    # free tier for the fast work. The Manifest gateway these replaced stopped
    # routing free accounts (M204), and its Claude route relied on a Claude
    # subscription, which may not be routed through a third-party gateway. See
    # services/llm_client.py for the tier table and the GPT-6 request rules.
    #
    #   fast     OPENROUTER_MODEL_FAST, then OPENAI_MODEL_FAST_FALLBACK
    #   quality  OPENAI_MODEL_QUALITY, then the free OpenRouter ladder
    #   tailor   OPENAI_MODEL_TAILOR, then the free OpenRouter ladder
    #
    # `quality` is what job-os-quality served (template builds from an
    # upload); `tailor` is what job-os-sonnet served (tailoring, review, cover
    # letters, interview prep, outreach, enrichment). Both default to Terra
    # ($1 in / $5 out per 1M tokens). gpt-6.1-sol ($2 / $10) is the upgrade
    # where quality matters most; it has no `reasoning_effort: none`, which
    # llm_client handles by sending `low` with extra token headroom.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model_quality: str = "gpt-6.1-terra"
    openai_model_tailor: str = "gpt-6.1-terra"
    # $0.10 in / $0.50 out per 1M tokens: the paid net under the free fast model.
    openai_model_fast_fallback: str = "gpt-6-luna"

    # Also the key for the free emergency ladder behind `quality` and `tailor`,
    # and for jd_parse's free-model racer. Unset, the fast tier goes straight
    # to OPENAI_MODEL_FAST_FALLBACK.
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Note: a 2026-08-29 benchmark (llm_client.OPENROUTER_FALLBACK_MODELS)
    # saw this model return truncated JSON on a full ParsedJD extraction.
    # A fast-tier reply that asks for JSON and does not parse fails over to
    # the OpenAI fallback, so a bad answer here costs one Luna call, not a
    # failed request.
    openrouter_model_fast: str = "nvidia/nemotron-3.5-lightning:free"

    # Thinking effort for the analyst call, unset by default, which means
    # `medium` (tailor.ANALYST_DEFAULT_EFFORT), the same as the model's own
    # default. It is here to be MEASURED, not to be changed: a trace of
    # four real runs put the analyst at 57% of tailoring wall clock (184s of
    # 321s) while carrying half the input of the compose call, so effort rather
    # than context size is the variable worth testing. Setting it to "medium"
    # is the experiment; leaving it unset is production.
    #
    # It is config rather than a constant because the tailor runs in the
    # Appwrite Function, which deploys from main, so a constant could not be
    # varied without shipping the very change the measurement is meant to
    # justify. See the comment on COMPOSE_EFFORT for why reducing power on this
    # specific step needs evidence: swapping the analyst to Haiku was tried and
    # lost real matches, Job Match 52.2 against 73.9 and 78.3. That was a MODEL
    # change; this is an EFFORT change, and it is the one that paid off on
    # compose.
    analyst_effort: str | None = None

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

    # `job_postings` (the crawl index) is a Neon Postgres table. It spent
    # 2026-08-18 to 2026-08-31 in the Appwrite project the resume workspace
    # uses, to escape Neon's free-tier storage cap, and came back because
    # Appwrite bills database reads PER ROW: the crawl alone measured ~1.19M
    # reads a month against a 1.75M allowance, and running out took resumes and
    # tailoring down with it. See `db/models/job_posting.py`.
    #
    # `appwrite_job_postings_table_id` below is kept, and is now only the
    # default table id `services/appwrite_tables.py` falls back to when a
    # caller names none. Every remaining caller (the application-card sync and
    # its backfill) passes `table_id=` explicitly, so nothing reaches it today.
    #
    # `services/appwrite_tables.py` talks to Appwrite's REST API directly over
    # `httpx`, authenticated with `appwrite_api_key`.
    # A first version of that module shelled out to the `appwrite` CLI's own
    # locally-authenticated session instead, which worked against the real
    # data during development and then failed in production with
    # `FileNotFoundError: 'appwrite'` -- the CLI binary was never in the
    # deploy image, and even installed, it should not have been running as a
    # developer's personal login inside a shared server process. `httpx`
    # against the documented REST API, scoped to a real key with only
    # `databases.read`/`databases.write`, is the version that is actually
    # meant to run here.
    appwrite_endpoint: str = "https://nyc.cloud.appwrite.io/v1"
    appwrite_project_id: str = "6a6552db0034a120b320"
    appwrite_api_key: str | None = None
    appwrite_database_id: str = "job-os"
    appwrite_job_postings_table_id: str = "job_postings"
    # The board reads these, not Postgres, so a job parsed in the background
    # has to be written back here or the card keeps its insert-time snapshot
    # forever. See services/jd_ingest.py::sync_job_into_cards.
    appwrite_application_cards_table_id: str = "application_cards"

    # Personal standalone job-scraper (separate project, separate infra) - see
    # job_os.ingest.scraper_import. Pull-only: this app reads its export, it
    # never gets write access to anything of ours. Unrelated to the
    # appwrite_* fields above - this is the scraper's own API, not job.os's.
    scraper_export_url: str | None = None
    scraper_export_key: str | None = None

    reactive_resume_base_url: str | None = None
    reactive_resume_api_key: str | None = None

    # ---- Email transport -----------------------------------------------------
    #
    # "console" writes the rendered message to a stream and sends nothing, which
    # is the only default that cannot mail a stranger by accident. Switching to
    # "resend" is a deliberate act that also requires a key and a from address.
    email_provider: str = Field(default="console")
    email_from: str | None = Field(
        default=None, description='Envelope sender, e.g. "job.os alerts <alerts@example.com>"'
    )
    email_reply_to: str | None = None
    resend_api_key: str | None = None
    resend_api_base: str = "https://api.resend.com"

    # ---- Job alerts ----------------------------------------------------------
    #
    # Master switch, separate from `email_provider`, for the same reason
    # `allow_anonymous_dev_user` is separate from `app_env`: a provider gets
    # configured for one feature (a password reset, say) and would otherwise
    # silently arm a scheduled sender that mails every user on the table.
    alerts_enabled: bool = Field(default=False)
    #: HMAC key for unsubscribe links. No default and no fallback: an
    #: unguessable-by-construction token that everyone can forge is worse than a
    #: feature that refuses to run, so the digest run fails closed without it.
    alert_unsubscribe_secret: str | None = None
    #: Public origin that serves GET/POST /api/v1/alerts/unsubscribe. The link
    #: has to work with no session, so it points at the API, not the Next.js
    #: proxy, which requires a Clerk cookie.
    alert_link_base_url: str | None = None
    #: Origin of the web app, for the "turn alerts back on" link on the
    #: unsubscribe confirmation page. A different value from
    #: `alert_link_base_url`: that one is the API, this one is where a signed-in
    #: user manages their alerts.
    alert_app_base_url: str | None = None
    #: CAN-SPAM requires a valid physical postal address in every commercial
    #: message. Unset means the digest will not send.
    alert_postal_address: str | None = None
    #: Floor between two `immediate` sends, so a source that republishes a batch
    #: every few minutes cannot turn into a mail loop.
    alert_immediate_min_interval_minutes: int = Field(default=60, ge=1)
    #: Cap on rows in one digest email. Beyond this the email says how many more
    #: were found rather than growing without bound.
    alert_max_jobs_per_digest: int = Field(default=25, ge=1, le=200)

    @field_validator("email_provider")
    @classmethod
    def _known_email_provider(cls, value: str) -> str:
        """Reject an unknown provider at startup rather than at send time.

        A typo in EMAIL_PROVIDER used to be the kind of thing you discover when
        the first digest run logs "unknown provider" and drops the send, hours
        after the deploy that caused it.
        """
        normalized = value.strip().lower()
        allowed = {"console", "resend"}
        if normalized not in allowed:
            raise ValueError(
                f"EMAIL_PROVIDER must be one of {sorted(allowed)}, got {value!r}"
            )
        return normalized

    @field_validator("openai_base_url", "openrouter_base_url")
    @classmethod
    def _normalize_llm_base_url(cls, value: str) -> str:
        """Keep the versioned base and drop anything after it.

        llm_client appends `/chat/completions` itself, so a value pasted with
        the endpoint on it (`.../v1/chat/completions`) or a trailing slash
        would otherwise produce `.../chat/completions/chat/completions`.
        """
        trimmed = value.strip().rstrip("/")
        if trimmed.endswith("/chat/completions"):
            trimmed = trimmed[: -len("/chat/completions")].rstrip("/")
        if not trimmed:
            raise ValueError("LLM base URL may not be empty")
        return trimmed

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
