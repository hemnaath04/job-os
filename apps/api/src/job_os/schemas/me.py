"""Schemas for the current-user (me) router: profile + settings.

Settings began as six fields, and every one of them described how to *present*
the user: a theme, a default resume, a function, a level, a location, a
timezone. Nothing described what to go and find. So the profile fed scoring and
tailoring while retrieval was retyped by hand into every search.

The search preferences below close that gap. Target titles, locations, work
authorization, a pay floor, a seniority band, work models and company lists are
all things the user states once and every search can then be seeded from.

Backward compatibility is a hard constraint here, not a nicety: `User.settings`
is a JSONB blob already written by earlier deploys, so

  - every field added below carries a default, and a row holding only the
    original six keys validates untouched;
  - nothing was renamed or removed. `default_location` still exists beside the
    new `locations` list, and the two are kept from contradicting each other
    (see `UserSettings._fill_locations`).
"""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from job_os.schemas.common import ORMModel

Theme = Literal["system", "dark", "light"]

WorkAuthorization = Literal[
    "us_citizen",
    "permanent_resident",
    "visa_holder_needs_transfer",
    "needs_sponsorship",
    "other",
]
"""The candidate's half of the eligibility question.

The employer's half is already read off the posting in
apps/web/src/lib/discover/work-auth.ts, and on its own it can only warn: "does
not sponsor" is disqualifying for one user and irrelevant to the next. Stored
beside a status it becomes a filter instead of a chip.

Unset and `other` mean different things. Unset is "never asked" and nothing
should be inferred from it; `other` is "asked, and none of the four fit".
"""

SeniorityLevel = Literal["intern", "new-grad", "mid", "senior", "staff"]
"""Same spellings as the existing `default_level` values, hyphen included, so
the two fields can never disagree about what a level is called."""

_SENIORITY_ORDER: tuple[SeniorityLevel, ...] = (
    "intern",
    "new-grad",
    "mid",
    "senior",
    "staff",
)
"""Ranked, so a min/max pair can be checked for being the right way round."""

WorkModel = Literal["onsite", "hybrid", "remote"]

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{3}$", to_upper=True)]
"""ISO-4217 alpha-3. Case-normalised rather than rejected: "usd" is a slip."""

# These lists live in a JSONB blob the client can PATCH, so an uncapped list is
# an uncapped write. Both caps are far above any real use.
_MAX_LIST_ITEMS = 50
_MAX_ITEM_CHARS = 120


def _clean_strings(values: list[str]) -> list[str]:
    """Trim, drop blanks, dedupe case-insensitively, keep the first spelling.

    Forgiving rather than strict on purpose: these are edited as chip inputs, so
    an empty entry or a title already in the list is a slip, not a reason to
    fail the whole save.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = value.strip()[:_MAX_ITEM_CHARS]
        folded = text.casefold()
        if not text or folded in seen:
            continue
        seen.add(folded)
        cleaned.append(text)
    return cleaned[:_MAX_LIST_ITEMS]


def _dedupe_work_models(values: list[WorkModel]) -> list[WorkModel]:
    """Order-preserving dedupe. A toggle sent twice is one choice, not two."""
    return list(dict.fromkeys(values))


StringList = Annotated[list[str], AfterValidator(_clean_strings)]
WorkModelList = Annotated[list[WorkModel], AfterValidator(_dedupe_work_models)]


class SeniorityRange(ORMModel):
    """An inclusive band over `_SENIORITY_ORDER`.

    Either end may be left open, which reads as "no bound on that side". An
    intern searching up to `mid` and a staff engineer searching down to `senior`
    are both one-sided, and defaulting the open end to the extreme would make
    the stored value claim more than the user said.
    """

    min: SeniorityLevel | None = None
    max: SeniorityLevel | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "SeniorityRange":
        if self.min is None or self.max is None:
            return self
        if _SENIORITY_ORDER.index(self.min) > _SENIORITY_ORDER.index(self.max):
            raise ValueError("seniority_range.min ranks above seniority_range.max")
        return self


class UserSettings(ORMModel):
    """Allowed keys in `User.settings`. Anything not in this schema is ignored
    on write — keeps the JSONB blob from accumulating arbitrary client junk."""

    theme: Theme = "dark"
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None

    # ── Search preferences ──
    # Read by discovery rather than by tailoring. Every one defaults to "no
    # opinion", so an account that never opens Settings searches exactly as it
    # did before these existed.

    target_titles: StringList = Field(
        default_factory=list,
        description="Roles to search for, most wanted first.",
    )
    """Plain strings today. A canonical title taxonomy is being built separately
    and these will become its leaf ids; storing text now means that lands as a
    backfill rather than a breaking change, and nothing here has to wait for
    it."""

    work_authorization: WorkAuthorization | None = None
    """Nullable because "never stated" is a real answer, and the only answer any
    row written before this field existed can give."""

    salary_floor: int | None = Field(default=None, ge=0, le=100_000_000)
    """Lowest acceptable base pay per year, in `salary_currency`."""

    salary_currency: CurrencyCode = "USD"

    seniority_range: SeniorityRange = Field(default_factory=SeniorityRange)

    work_models: WorkModelList = Field(default_factory=list)
    """Empty means all three are acceptable, not none of them."""

    target_companies: StringList = Field(default_factory=list)
    """Companies to surface first. Not a whitelist: it ranks, it does not gate."""

    excluded_companies: StringList = Field(default_factory=list)
    """Companies to drop outright. This one does gate."""

    max_job_age_days: int = Field(default=30, ge=1, le=180)
    """Seeds `DiscoverySearchRequest.max_age_days`, and carries its bounds so a
    stored preference can never be a value a search would reject."""

    locations: StringList = Field(default_factory=list)
    """Where to search: cities, regions, or "Remote". Empty means anywhere.

    A list rather than the single `default_location` it sits beside, because one
    location was never the real shape of the question: Boston or NYC or remote
    is the ordinary case, and expressing it meant running the search three
    times."""

    @model_validator(mode="after")
    def _fill_locations(self) -> "UserSettings":
        """Keep `default_location` and `locations` from contradicting each other.

        Only ever fills a blank side from a set one. A row written before
        `locations` existed carries a `default_location` and no list, and would
        otherwise read back as "search anywhere"; filling the list from it makes
        every one of those rows correct with no data migration. The reverse fill
        keeps whatever still reads only `default_location` (the Job Finder does)
        seeing a real value.

        Deliberately not a reconciliation: when both sides are set, both are left
        alone. Preferring one here would make a PATCH of the other silently do
        nothing, so which side wins on a write is settled in the router, where
        what the client actually sent is known.
        """
        location = (self.default_location or "").strip()
        if not self.locations and location:
            self.locations = [location]
        elif self.locations and not location:
            self.default_location = self.locations[0]
        return self


class UserSettingsPatch(ORMModel):
    """Partial update: every field optional; only provided keys are applied.

    Mirrors `UserSettings` field for field. A field added there and forgotten
    here is silently unpatchable, which is the one failure mode worth watching
    for; `tests/test_user_settings.py` asserts the two stay in step.

    Fields that `UserSettings` declares non-nullable are still optional here,
    and an explicit `null` for one of them is dropped by the router rather than
    merged: null on a field with a real default means "no opinion", and merging
    it would fail validation.
    """

    theme: Theme | None = None
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None
    target_titles: StringList | None = None
    work_authorization: WorkAuthorization | None = None
    salary_floor: int | None = Field(default=None, ge=0, le=100_000_000)
    salary_currency: CurrencyCode | None = None
    seniority_range: SeniorityRange | None = None
    work_models: WorkModelList | None = None
    target_companies: StringList | None = None
    excluded_companies: StringList | None = None
    max_job_age_days: int | None = Field(default=None, ge=1, le=180)
    locations: StringList | None = None


class MeRead(ORMModel):
    """The current user's identity + settings — drives the Settings page."""

    id: UUID
    email: str
    display_name: str | None
    settings: UserSettings
