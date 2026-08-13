"""The enrichment worker, against mocked gateway replies.

Enrichment sits in the ingest path, so the property that matters most is not
accuracy but totality: a posting the model mangled, refused, or answered with
prose still has to reach the index, carrying a visible record that it was never
really enriched. Every test here is a way the model can fail, and the assertion
is always that the job survives it.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import anthropic
import httpx
import pytest

from job_os.schemas.enrichment import ENRICHMENT_SCHEMA_VERSION, JobEnrichment
from job_os.services import job_enrich
from job_os.services.job_enrich import (
    ENRICHMENT_KEY,
    enrich_job,
    load_enrichment,
    store_enrichment,
)

JD = """
Senior Backend Engineer, Acme Cloud

We are hiring a senior backend engineer in Boston, MA (hybrid).

Minimum qualifications:
  - Bachelor's degree in Computer Science or a related field
  - 5+ years of backend engineering experience
  - Strong Python and Go
  - Experience with Kubernetes and AWS

Preferred:
  - Terraform
  - Observability tooling such as Prometheus or Grafana

Compensation: $180,000 to $220,000 per year. We sponsor visas.
"""

# What a well behaved reply looks like. Deliberately not every field: the schema
# defaults are part of the contract, and a worker that only works on complete
# replies is a worker that fails on most real ones.
GOOD_REPLY = {
    "core_job_title": "Backend Engineer",
    "job_family": "software-engineering",
    "company_industry": "Information Technology",
    "seniority_level": "senior",
    "role_type": "individual-contributor",
    "min_years_experience": 5,
    "years_experience_mentioned": True,
    "requirements_summary": "Senior backend engineer with Python, Go, Kubernetes and AWS.",
    "requirements_prose": {
        "must_have": [
            "Bachelor's degree in Computer Science or a related field",
            "5+ years of backend engineering experience",
        ],
        "preferred": ["Terraform", "Observability tooling such as Prometheus or Grafana"],
    },
    "skills": [
        {"skill": "Python", "importance": 3, "necessity": "required", "evidence": "Strong Python"},
        {"skill": "Go", "importance": 3, "necessity": "required", "evidence": "Strong Go"},
        {"skill": "Kubernetes", "importance": 2, "necessity": "required"},
        {"skill": "AWS", "importance": 2, "necessity": "required"},
        {"skill": "Terraform", "importance": 1, "necessity": "preferred"},
        {"skill": "Prometheus", "importance": 1, "necessity": "preferred"},
    ],
    "education": {"bachelors": {"status": "required", "fields_of_study": ["Computer Science"]}},
    "compensation": {
        "currency": "USD",
        "listed_frequency": "yearly",
        "listed_min": 180000,
        "listed_max": 220000,
    },
    "workplace": {
        "workplace_type": "hybrid",
        "locations": [{"city": "Boston", "state": "MA", "country": "US"}],
    },
    "eligibility": {"visa_sponsorship": "yes"},
    "commitment": ["full-time"],
}


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, text: str, *, stop_reason: str = "end_turn") -> None:
        self.content = [_Block(text)] if text else []
        self.stop_reason = stop_reason
        self.usage = type("Usage", (), {"output_tokens": len(text)})()


class _Gateway:
    """A stand-in for the Manifest gateway that records instead of sending.

    Nothing in this file may reach the network. `create_message` is patched at the
    `job_enrich` module boundary rather than deeper, so the worker's own retry and
    salvage logic is what runs, and the only thing replaced is the socket.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replies: list[Any] = []

    def queue(self, *items: Any) -> None:
        """Replies for successive calls. An Exception is raised instead."""
        self.replies.extend(items)

    async def __call__(self, client: Any, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        if not self.replies:
            return _Message(json.dumps(GOOD_REPLY))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Message(reply)

    @property
    def call_count(self) -> int:
        return len(self.sent)

    def prompt(self, index: int = 0) -> str:
        content = self.sent[index]["messages"][0]["content"]
        assert isinstance(content, str)
        return content


@pytest.fixture(autouse=True)
def gateway(monkeypatch: pytest.MonkeyPatch) -> _Gateway:
    """Every test gets a key that looks present and a gateway that never dials.

    Autouse, because a test that forgot to take the fixture would fall through to
    a real client. The key is patched onto the settings object rather than the
    environment: `Settings` reads a `.env` file, so `delenv` alone leaves a real
    key in place, and a test meaning to exercise the no-key path would instead
    exercise the network.
    """
    from job_os.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings().model_copy(
        update={
            "anthropic_api_key": "test-key",
            "anthropic_base_url": "https://gateway.invalid",
            "anthropic_model_extract": "manifest/auto",
        }
    )
    monkeypatch.setattr(job_enrich, "get_settings", lambda: settings)
    fake = _Gateway()
    monkeypatch.setattr(job_enrich, "create_message", fake)
    return fake


# --- the happy path ---------------------------------------------------------


async def test_a_clean_reply_produces_a_complete_document(gateway: _Gateway) -> None:
    result = await enrich_job(JD, title_hint="Senior Backend Engineer | Acme")

    assert result.extraction_gaps == []
    assert result.schema_version == ENRICHMENT_SCHEMA_VERSION
    assert result.core_job_title == "Backend Engineer"
    assert result.seniority_level == "senior"
    assert result.min_years_experience == 5
    assert result.years_experience_mentioned is True
    assert result.education.bachelors.status == "required"
    assert result.eligibility.visa_sponsorship == "yes"


async def test_provenance_is_stamped_by_code_not_the_model(gateway: _Gateway) -> None:
    """Which model, which schema, and when, all recorded without being asked for.

    A document that cannot say what produced it cannot be re-enriched selectively
    when a prompt or a schema changes.
    """
    result = await enrich_job(JD)
    assert result.model
    assert result.enriched_at is not None
    assert result.enriched_at.tzinfo is not None


async def test_the_six_frequencies_are_derived_from_the_one_stated(gateway: _Gateway) -> None:
    """The model states one figure and code produces the rest, exactly.

    Asking a language model for six multiplications per job buys six chances to
    be wrong about something arithmetic settles for free.
    """
    result = await enrich_job(JD)
    comp = result.compensation
    assert comp.yearly_min == 180000
    assert comp.monthly_min == 15000
    assert comp.hourly_min == round(180000 / 2080)
    assert comp.is_transparent is True


async def test_canonical_keys_are_filled_for_every_skill(gateway: _Gateway) -> None:
    result = await enrich_job(JD)
    assert all(item.canonical for item in result.skills)
    assert {item.canonical for item in result.skills} >= {"python", "go", "kubernetes", "aws"}


async def test_the_gateway_is_called_the_way_this_codebase_calls_it(gateway: _Gateway) -> None:
    """One call, on the Sonnet tier, with the schema in the prompt.

    The tier matters more here than anywhere else in the product: enrichment runs
    once per job across the whole corpus, so it is the single biggest lever on
    ingest cost.
    """
    await enrich_job(JD)
    assert gateway.call_count == 1
    assert gateway.sent[0]["extra_headers"]["x-manifest-tier"] == "job-os-sonnet"
    assert gateway.sent[0]["max_tokens"] == job_enrich.MAX_TOKENS
    assert "one raw JSON object" in gateway.prompt()


async def test_one_llm_call_per_job(gateway: _Gateway) -> None:
    """The economics of the whole design, asserted.

    Enrichment is O(jobs) and matching is O(1). A second call per job would
    double the corpus cost, so the count is a test rather than a comment.
    """
    for _ in range(3):
        await enrich_job(JD)
    assert gateway.call_count == 3


async def test_the_prompt_does_not_ask_for_fields_code_owns(gateway: _Gateway) -> None:
    """Derived fields are absent from the schema the model sees.

    Leaving them in invites the model to spend output tokens on them and to
    disagree with the code that is about to overwrite them.
    """
    await enrich_job(JD)
    prompt = gateway.prompt()
    schema = json.loads(prompt[prompt.index('{"$defs"') :]) if '{"$defs"' in prompt else None
    assert schema is not None
    assert "canonical" not in schema["$defs"]["SkillRequirement"]["properties"]
    assert "yearly_min" not in schema["$defs"]["Compensation"]["properties"]
    assert "schema_version" not in schema["properties"]


# --- graceful partial extraction --------------------------------------------


async def test_unparseable_compensation_costs_only_the_compensation(gateway: _Gateway) -> None:
    """The requirement stated plainly: a job with bad comp still indexes.

    A single `model_validate` would have thrown the skills, the education and the
    eligibility away along with the bad field, turning one wrong value into an
    unmatchable job.
    """
    broken = {
        **GOOD_REPLY,
        "compensation": {"listed_frequency": "per fortnight", "listed_min": "a lot"},
    }
    gateway.queue(json.dumps(broken))

    result = await enrich_job(JD)

    assert "compensation" in result.extraction_gaps
    assert result.compensation.yearly_min is None
    # Everything else survived.
    assert len(result.skills) == 6
    assert result.education.bachelors.status == "required"
    assert result.eligibility.visa_sponsorship == "yes"
    assert result.seniority_level == "senior"


async def test_an_invented_enum_value_costs_only_that_section(gateway: _Gateway) -> None:
    broken = {**GOOD_REPLY, "job_family": "underwater-basket-weaving"}
    gateway.queue(json.dumps(broken))

    result = await enrich_job(JD)

    assert "job_family" in result.extraction_gaps
    assert result.job_family == "other"
    assert len(result.skills) == 6


async def test_a_units_error_in_compensation_is_dropped_not_indexed(gateway: _Gateway) -> None:
    """A figure that cannot be right is worse than no figure.

    A model reading "$95/hour" as a yearly salary would put a real job into
    salary filters at a hundredth of its actual pay, and nothing downstream would
    ever question it.
    """
    absurd = {
        **GOOD_REPLY,
        "compensation": {"listed_frequency": "yearly", "listed_min": 95, "listed_max": 95},
    }
    gateway.queue(json.dumps(absurd))

    result = await enrich_job(JD)

    assert "compensation_implausible" in result.extraction_gaps
    assert result.compensation.yearly_min is None
    assert result.compensation.is_transparent is False
    assert len(result.skills) == 6


async def test_an_inverted_compensation_range_is_dropped(gateway: _Gateway) -> None:
    inverted = {
        **GOOD_REPLY,
        "compensation": {"listed_frequency": "yearly", "listed_min": 220000, "listed_max": 180000},
    }
    gateway.queue(json.dumps(inverted))
    result = await enrich_job(JD)
    assert "compensation_implausible" in result.extraction_gaps


# --- the model misbehaving --------------------------------------------------


async def test_a_fenced_reply_is_read_anyway(gateway: _Gateway) -> None:
    """Reuses the repair `llm_json` already does for every agent here."""
    gateway.queue(f"Sure, here you go:\n```json\n{json.dumps(GOOD_REPLY)}\n```\nHope that helps.")
    result = await enrich_job(JD)
    assert result.extraction_gaps == []
    assert result.core_job_title == "Backend Engineer"


async def test_an_empty_reply_is_asked_again_once(gateway: _Gateway) -> None:
    """An empty reply is a recoverable event, not a user-facing failure."""
    gateway.queue("", json.dumps(GOOD_REPLY))

    result = await enrich_job(JD)

    assert gateway.call_count == 2
    assert result.extraction_gaps == []
    # The retry carries the corrective instruction, not just the same prompt.
    assert job_enrich.JSON_ONLY_RETRY in gateway.sent[1]["messages"][-1]["content"]


async def test_two_empty_replies_still_yield_an_indexable_job(gateway: _Gateway) -> None:
    gateway.queue("", "")
    result = await enrich_job(JD, title_hint="Senior Backend Engineer")
    assert result.extraction_gaps == ["empty_reply"]
    assert result.core_job_title == "Senior Backend Engineer"
    assert result.skills == []


async def test_prose_instead_of_json_still_yields_an_indexable_job(gateway: _Gateway) -> None:
    gateway.queue("I would be happy to help you with that job posting!")
    result = await enrich_job(JD, title_hint="Senior Backend Engineer")
    assert result.extraction_gaps == ["invalid_json"]
    assert result.core_job_title == "Senior Backend Engineer"


async def test_a_json_array_instead_of_an_object_is_handled(gateway: _Gateway) -> None:
    gateway.queue("[1, 2, 3]")
    result = await enrich_job(JD)
    assert result.extraction_gaps == ["invalid_json"]


async def test_a_dead_gateway_does_not_lose_the_job(gateway: _Gateway) -> None:
    """`create_message` has already exhausted its retries by this point.

    So this is a real outage, and the right behaviour is a row that says so
    rather than an exception that aborts the batch.
    """
    gateway.queue(
        anthropic.APIStatusError(
            "gateway down",
            response=httpx.Response(500, request=httpx.Request("POST", "https://gateway.invalid")),
            body=None,
        )
    )
    result = await enrich_job(JD, title_hint="Senior Backend Engineer")
    assert result.extraction_gaps == ["gateway_error"]
    assert result.core_job_title == "Senior Backend Engineer"


async def test_a_missing_key_is_reported_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch, gateway: _Gateway
) -> None:
    """No key configured is a deployment fact, reported as one.

    Patched on the settings object rather than the environment, because
    `Settings` reads a `.env` file and clearing the variable alone would leave a
    real key in place and send this test at the network.
    """
    from job_os.settings import get_settings

    settings = get_settings().model_copy(update={"anthropic_api_key": None})
    monkeypatch.setattr(job_enrich, "get_settings", lambda: settings)

    result = await enrich_job(JD, title_hint="Backend Engineer")

    assert result.extraction_gaps == ["no_api_key"]
    assert result.core_job_title == "Backend Engineer"
    assert gateway.call_count == 0


async def test_an_empty_posting_is_not_sent_to_the_model(gateway: _Gateway) -> None:
    """A blank JD is a scraper failure, and paying to confirm that is waste."""
    result = await enrich_job("   ")
    assert result.extraction_gaps == ["empty_jd"]
    assert gateway.call_count == 0


# --- honesty about dates ----------------------------------------------------


async def test_a_real_posted_date_is_not_labelled_an_estimate(gateway: _Gateway) -> None:
    """The reference calls the field `estimated` because a crawled date is inferred.

    When the source actually gave us one, saying so is the difference between
    keeping their honesty and merely copying their field name.
    """
    posted = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    result = await enrich_job(JD, posted_at=posted)
    assert result.estimated_publish_date == posted
    assert result.publish_date_is_estimated is False


async def test_an_absent_posted_date_stays_flagged_as_estimated(gateway: _Gateway) -> None:
    reply = {**GOOD_REPLY, "estimated_publish_date": "2026-08-10T00:00:00Z"}
    gateway.queue(json.dumps(reply))
    result = await enrich_job(JD)
    assert result.publish_date_is_estimated is True


# --- storage ----------------------------------------------------------------


async def test_storing_preserves_what_the_earlier_parser_wrote(gateway: _Gateway) -> None:
    """Landing in `jd_parsed` needs no migration, and must not overwrite.

    `jd_parse.py` writes the same column at import and parts of the tailor path
    still read it. Two passes with two schemas can share the column as long as
    each stays in its own key.
    """
    existing = {"title": "Senior Backend Engineer", "required_skills": ["Python"]}
    result = await enrich_job(JD)

    merged = store_enrichment(existing, result)

    assert merged["title"] == "Senior Backend Engineer"
    assert merged["required_skills"] == ["Python"]
    assert merged[ENRICHMENT_KEY]["core_job_title"] == "Backend Engineer"


async def test_storing_does_not_mutate_the_caller_s_dict(gateway: _Gateway) -> None:
    existing: dict[str, Any] = {"title": "x"}
    result = await enrich_job(JD)
    store_enrichment(existing, result)
    assert ENRICHMENT_KEY not in existing


async def test_a_stored_document_round_trips(gateway: _Gateway) -> None:
    result = await enrich_job(JD)
    reloaded = load_enrichment(store_enrichment({}, result))
    assert reloaded is not None
    assert reloaded.model_dump(mode="json") == result.model_dump(mode="json")


def test_a_job_with_no_enrichment_reads_as_none() -> None:
    assert load_enrichment(None) is None
    assert load_enrichment({}) is None
    assert load_enrichment({"title": "x"}) is None


def test_an_unreadable_stored_document_reads_as_none_rather_than_raising() -> None:
    """A rollback must find jobs it cannot read, not a server that will not start.

    Returning None puts those jobs on the same path as jobs that were never
    enriched, which is a path that already exists and already works.
    """
    from_the_future = {ENRICHMENT_KEY: {"schema_version": 99, "job_family": "teleportation"}}
    assert load_enrichment(from_the_future) is None


def test_a_document_stored_as_json_survives_the_column_round_trip() -> None:
    """JSONB is the storage, so every read is a `json.loads` of a `json.dumps`."""
    original = JobEnrichment(core_job_title="Backend Engineer")
    column = json.loads(json.dumps(store_enrichment({}, original)))
    reloaded = load_enrichment(column)
    assert reloaded is not None
    assert reloaded.core_job_title == "Backend Engineer"
