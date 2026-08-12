"""The enrichment schema, checked against real records from two live products.

The fixtures are not invented. Each one is a real job from a captured payload,
mapped field by field into `JobEnrichment`; `tests/fixtures/enrichment/README.md`
records which record and which mapping. A schema that cannot hold real data from
the products it was modelled on is a schema that will not survive its own ingest
path, and inventing the test data is exactly how that goes unnoticed.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from job_os.schemas.enrichment import (
    ENRICHMENT_SCHEMA_VERSION,
    SKILL_ALIASES,
    Compensation,
    EducationRequirements,
    JobEnrichment,
    SkillRequirement,
    canonical_skill,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "enrichment"
FIXTURE_NAMES = (
    "cisco_cloud_engineer",
    "worlds_ml_research_intern",
    "vienna_fullstack_engineer",
    "first_tee_play9_intern",
)


def load(name: str) -> JobEnrichment:
    return JobEnrichment.model_validate_json((FIXTURES / f"{name}.json").read_text())


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_real_records_validate(name: str) -> None:
    job = load(name)
    assert job.schema_version == ENRICHMENT_SCHEMA_VERSION
    assert job.core_job_title
    # A document that validated but recorded a gap would mean the fixture was
    # captured from a failed enrichment, which would make every other assertion
    # about it meaningless.
    assert job.extraction_gaps == []


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_round_trips_through_storage(name: str) -> None:
    """Dumped and reloaded is the same document.

    `Job.jd_parsed` is JSONB, so every document makes this trip on every read.
    A validator that computes a derived field one way on construction and another
    way on reload would drift silently, and the drift would only show up as two
    users seeing different scores for the same job.
    """
    job = load(name)
    again = JobEnrichment.model_validate(json.loads(json.dumps(job.model_dump(mode="json"))))
    assert again.model_dump(mode="json") == job.model_dump(mode="json")


def test_skills_carry_importance_and_necessity() -> None:
    """The atomized half of the requirements, which is what the matcher reads."""
    job = load("cisco_cloud_engineer")
    assert len(job.required_skills()) > 0
    assert len(job.preferred_skills()) > 0
    assert {item.importance for item in job.skills} <= {1, 2, 3}
    # Every skill has a canonical key, or it can never match anything.
    assert all(item.canonical for item in job.skills)


def test_requirements_kept_in_both_forms() -> None:
    """Prose for the human, atoms for the matcher, and neither substitutes.

    Jobright ships both and that is the detail that makes it work. The prose
    retains the qualifier atomizing destroys: this posting's real requirement is
    experience in "AWS, Azure, or Google Cloud", and the atomized list cannot
    express the "or".
    """
    job = load("cisco_cloud_engineer")
    assert job.requirements_prose.must_have
    assert job.requirements_prose.preferred
    assert any("or Google Cloud" in item for item in job.requirements_prose.must_have)
    assert len(job.skills) > len(job.requirements_prose.must_have)


def test_degrees_are_atomized_per_level() -> None:
    """One posting really did require a bachelors, a masters AND a doctorate.

    The Worlds research internship asks for a completed BS while the candidate is
    enrolled in an MS or PhD. Any collapse of that into a single minimum-degree
    field is wrong in one direction or the other, which is why the reference
    atomizes per level and why this schema does too.
    """
    job = load("worlds_ml_research_intern")
    assert job.education.bachelors.status == "required"
    assert job.education.masters.status == "required"
    assert job.education.doctorate.status == "required"
    assert "Computer Science" in job.education.masters.fields_of_study
    assert job.education.highest_required() == "doctorate"


def test_not_mentioned_is_a_value_not_a_null() -> None:
    """Silence about a degree is a statement, and a different one from a failure."""
    job = load("vienna_fullstack_engineer")
    assert job.education.bachelors.status == "not-mentioned"
    assert job.education.highest_required() == "none"
    assert job.eligibility.visa_sponsorship == "not-mentioned"


def test_compensation_derivation_matches_the_reference_exactly() -> None:
    """The six frequencies, against the reference's own published numbers.

    hiring.cafe listed a $15/hour job as yearly 31200, monthly 2600, weekly 600,
    bi-weekly 1200 and daily 120. Reproducing those exactly is what keeps figures
    from the two corpora comparable, and it is the check that the conversion
    basis was recovered correctly rather than guessed.
    """
    comp = Compensation(listed_frequency="hourly", listed_min=15, listed_max=15, currency="USD")
    assert comp.yearly_min == 31200
    assert comp.monthly_min == 2600
    assert comp.weekly_min == 600
    assert comp.bi_weekly_min == 1200
    assert comp.daily_min == 120
    assert comp.hourly_min == 15


def test_compensation_derives_from_any_stated_frequency() -> None:
    """A yearly figure and an hourly figure for the same pay agree.

    Filtering on any basis has to be a comparison rather than a re-derivation, so
    the two directions must land on the same numbers.
    """
    yearly = Compensation(listed_frequency="yearly", listed_min=31200, listed_max=31200)
    hourly = Compensation(listed_frequency="hourly", listed_min=15, listed_max=15)
    assert yearly.hourly_min == hourly.hourly_min == 15
    assert yearly.yearly_min == hourly.yearly_min == 31200


def test_a_stated_figure_means_transparent_pay() -> None:
    """Transparency is derived from the figures, not taken on the model's word.

    The reference sample contains rows whose `is_compensation_transparent` and
    whose actual figures disagree. A stated number IS the definition, so code
    settles it.
    """
    comp = Compensation(
        is_transparent=False, listed_frequency="yearly", listed_min=155000, listed_max=223000
    )
    assert comp.is_transparent is True


def test_no_figures_means_no_derived_figures() -> None:
    """A posting that hid its salary must not acquire one.

    The Vienna posting carries a currency and a frequency and no numbers, which
    is the common shape. Inventing a range from that would put the job into
    salary filters it has no business being in.
    """
    job = load("vienna_fullstack_engineer")
    assert job.compensation.is_transparent is False
    assert job.compensation.yearly_min is None
    assert job.compensation.hourly_max is None


def test_publish_date_honesty_survives_reading_only_values() -> None:
    job = load("worlds_ml_research_intern")
    assert job.estimated_publish_date is not None
    assert job.publish_date_is_estimated is True


def test_location_count_is_derived_not_trusted() -> None:
    job = load("cisco_cloud_engineer")
    assert job.workplace.location_count == len(job.workplace.locations) == 1
    assert job.workplace.locations[0].city == "Milpitas"


# --- the canonicalization contract ------------------------------------------
#
# Both sides of a match pass through `canonical_skill`, so these are not style
# tests. A form that normalizes differently on the two sides is a skill that can
# never match, and that failure is invisible in production because it looks
# exactly like a candidate who lacks the skill.


def test_every_alias_value_is_already_canonical() -> None:
    """The guard on the table itself.

    An alias whose value does not survive its own normalizer points at a key
    nothing else can reach. This caught a real bug: with singularization applied
    after alias resolution, `k8s` resolved to "kubernetes" while "Kubernetes"
    reduced to "kubernete", so the two never met.
    """
    broken = {key: value for key, value in SKILL_ALIASES.items() if canonical_skill(value) != value}
    assert broken == {}


def test_canonicalization_is_idempotent() -> None:
    """Normalizing a canonical key must not change it.

    A key that shifts on a second pass would split one skill into two across a
    re-enrichment, and the two halves would stop matching each other.
    """
    for form in (*SKILL_ALIASES, *SKILL_ALIASES.values()):
        once = canonical_skill(form)
        assert canonical_skill(once) == once, form


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Kubernetes", "k8s"),
        ("Node.js", "nodejs"),
        ("CI/CD", "cicd"),
        ("REST APIs", "restful"),
        ("LLMs", "Large Language Models"),
        ("Microservices", "microservice"),
        ("AWS", "Amazon Web Services"),
        ("Vector Databases", "Qdrant"),
        ("Fine-tuning", "LoRA"),
        ("Transformers", "transformer"),
        ("GCP", "Google Cloud Platform"),
    ],
)
def test_equivalent_forms_share_a_key(left: str, right: str) -> None:
    assert canonical_skill(left) == canonical_skill(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # The three that a naive punctuation strip merges, and the one a naive
        # substring match merges.
        ("C", "C++"),
        ("C++", "C#"),
        ("Java", "JavaScript"),
        ("PyTorch", "TensorFlow"),
    ],
)
def test_distinct_skills_keep_distinct_keys(left: str, right: str) -> None:
    assert canonical_skill(left) != canonical_skill(right)


@pytest.mark.parametrize("form", ["AWS", "CSS", "iOS"])
def test_short_acronyms_survive_the_plural_stripper(form: str) -> None:
    assert canonical_skill(form) == form.lower()


def test_duplicate_skills_collapse_to_the_stronger_claim() -> None:
    """Two spellings of one skill must not inflate the denominator twice.

    A model asked for a skill list sometimes emits both "Kubernetes" and "K8s".
    Counting them separately would charge a candidate twice for one gap, and the
    extra denominator entry would read to the user as an unexplained lost point.
    """
    job = JobEnrichment(
        skills=[
            SkillRequirement(skill="Kubernetes", importance=1, necessity="preferred"),
            SkillRequirement(skill="K8s", importance=3, necessity="required"),
        ]
    )
    assert len(job.skills) == 1
    assert job.skills[0].importance == 3
    assert job.skills[0].necessity == "required"


def test_unmatchable_skills_are_dropped() -> None:
    """Punctuation and empty strings never reach the denominator.

    A requirement no profile can ever match is a guaranteed lost point with no
    reason attached, which is precisely the residual the scorer promises not to
    have.
    """
    job = JobEnrichment(
        skills=[
            SkillRequirement(skill="  "),
            SkillRequirement(skill="---"),
            SkillRequirement(skill="Python"),
        ]
    )
    assert [item.canonical for item in job.skills] == ["python"]


def test_canonical_is_recomputed_not_trusted() -> None:
    """The match key is code's to decide, never the model's.

    Accepting a canonical key from an LLM would make the one thing both sides
    have to agree on the one thing neither side controls.
    """
    item = SkillRequirement(skill="Kubernetes", canonical="something-the-model-made-up")
    assert item.canonical == canonical_skill("Kubernetes")


def test_highest_required_ignores_preferred_degrees() -> None:
    """A preferred degree does not gate, so it is not a floor.

    Treating "masters preferred" as a requirement would deduct the full
    level-short penalty from every candidate holding exactly the bachelors the
    posting actually asked for.
    """
    education = EducationRequirements.model_validate(
        {
            "bachelors": {"status": "required"},
            "masters": {"status": "preferred"},
        }
    )
    assert education.highest_required() == "bachelors"
    assert education.highest_preferred() == "masters"
