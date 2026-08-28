"""Projects are ranked by the kind of work the job is, not just by word overlap.

`_project_relevance` counts how many of a posting's requirements a project names.
That is the right primary signal and it is blind to one thing a reader is not:
a platform posting that mentions computer vision once produces a vision
requirement, a small vision side project matches it, and the candidate's
strongest backend work ties with it on one match apiece. The tie then fell to
whichever had a URL recorded, which is how a backend project came off a backend
job.

The rule these pin down is narrow on purpose. A lane breaks a tie. It never
lifts a project over one that matched more requirements, because the count is
still the better evidence when the two disagree.

Fixtures are a generic candidate with a generic stack.
"""
from __future__ import annotations

from datetime import date

from job_os.services.role_lane import jd_lane, lane_profile
from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _evidence_rank,
    _project_relevance,
    _Requirement,
)

# The repro posting: a platform job that names one model-shaped thing in passing.
PLATFORM_POSTING = {
    "title": "Backend Engineer Intern, Serving Platform",
    "function": "swe",
    "required_skills": ["Python", "Kubernetes", "distributed systems"],
    "technologies": ["PostgreSQL", "computer vision"],
}
PLATFORM_JD = (
    "You will build and operate the backend services behind our model serving "
    "platform: REST APIs, queue consumers, PostgreSQL schemas and the "
    "Kubernetes deployments they run on. Latency and throughput are the job. "
    "Some of the traffic is computer vision inference, so exposure there is a "
    "plus but not required."
)

REQUIREMENTS = [
    _Requirement(label="Python", alternatives=("Python",), preferred=False),
    _Requirement(label="Kubernetes", alternatives=("Kubernetes",), preferred=False),
    _Requirement(
        label="distributed systems",
        alternatives=("distributed systems",),
        preferred=False,
    ),
    _Requirement(label="PostgreSQL", alternatives=("PostgreSQL",), preferred=False),
    _Requirement(
        label="computer vision", alternatives=("computer vision",), preferred=True
    ),
]

BACKEND_PROJECT = TailorFact(
    id="ledger",
    kind="project",
    title="Ledger Sync",
    start_date=date(2025, 3, 1),
    payload={
        "description": (
            "A Python service that reconciles bank feeds. REST API, a queue "
            "consumer and a PostgreSQL schema, deployed on Kubernetes."
        )
    },
)
VISION_PROJECT = TailorFact(
    id="petcam",
    kind="project",
    title="Pet Cam Classifier",
    start_date=date(2025, 6, 1),
    end_date=date(2025, 7, 1),
    source_url="https://example.com/petcam",
    payload={
        "description": (
            "A small computer vision model that classifies pet photos. Trained "
            "with PyTorch, ran inference on a laptop."
        )
    },
)
BULLETS: dict[str, list[TailorBullet]] = {
    "ledger": [TailorBullet(id="b1", fact_id="ledger", text="Wrote the reconciliation service.")],
    "petcam": [TailorBullet(id="b2", fact_id="petcam", text="Trained the classifier.")],
}


# --- reading the posting ------------------------------------------------------


def test_a_platform_posting_that_mentions_vision_is_still_a_platform_posting() -> None:
    assert jd_lane(PLATFORM_POSTING, PLATFORM_JD) == "backend"


def test_a_models_posting_reads_as_models() -> None:
    posting = {
        "title": "Machine Learning Engineer Intern",
        "function": "ml",
        "required_skills": ["PyTorch", "computer vision"],
    }
    jd = (
        "Train and evaluate detection and segmentation models. You will own "
        "the training loop, run inference benchmarks and tune hyperparameters."
    )
    assert jd_lane(posting, jd) == "ml"


def test_a_posting_that_commits_to_nothing_gets_no_lane() -> None:
    """A generic posting must not reorder anything on two words of noise."""
    assert jd_lane({"title": "Software Engineer Intern"}, "Join a great team.") is None


def test_the_title_carries_more_than_a_passing_mention_in_the_body() -> None:
    # "Backend Engineer, Computer Vision Platform" is a backend job. The title
    # is the part of a posting that says which team you join.
    posting = {"title": "Backend Engineer Intern, Computer Vision Platform"}
    jd = "Build the APIs, services and Kubernetes deployments behind our models."
    assert jd_lane(posting, jd) == "backend"


# --- ranking projects ---------------------------------------------------------


def scored_by_id(*posting_lanes: str) -> dict[str, object]:
    scored = _project_relevance(
        [BACKEND_PROJECT, VISION_PROJECT], BULLETS, REQUIREMENTS, lanes=posting_lanes
    )
    return {item.fact_id: item for item in scored}


def test_the_backend_project_is_marked_as_this_postings_kind_of_work() -> None:
    by_id = scored_by_id("backend")

    assert by_id["ledger"].lane_match
    assert not by_id["petcam"].lane_match


def test_a_tie_on_requirements_is_broken_towards_the_lane() -> None:
    """The exact repro.

    The vision project has a recorded URL and the backend one does not, which
    used to decide it. The lane is read first, so the project that is the same
    kind of engineering as the job wins the tie.
    """
    tie = [
        _Requirement(
            label="computer vision", alternatives=("computer vision",), preferred=False
        ),
        _Requirement(label="Python", alternatives=("Python",), preferred=False),
    ]
    scored = _project_relevance(
        [VISION_PROJECT, BACKEND_PROJECT], BULLETS, tie, lanes=("backend",)
    )

    assert [item.fact_id for item in scored][0] == "ledger"
    assert scored[0].score == scored[1].score, "this was genuinely a tie on the count"


def test_a_lane_never_beats_a_higher_requirement_count() -> None:
    """The boundary that keeps this honest.

    Against a models posting the backend project still matches more of what is
    asked for, and it stays first. A lane reorders equals; it does not overrule
    the evidence.
    """
    scored = _project_relevance(
        [VISION_PROJECT, BACKEND_PROJECT], BULLETS, REQUIREMENTS, lanes=("ml",)
    )

    assert scored[0].fact_id == "ledger"
    assert scored[0].score > scored[1].score


def test_the_lane_sits_above_the_url_in_the_tiebreak() -> None:
    by_id = scored_by_id("backend")

    assert _evidence_rank(by_id["petcam"]) < _evidence_rank(by_id["ledger"]), (
        "a recorded URL must not outrank being the right kind of work"
    )


def test_no_lane_leaves_the_old_tiebreak_exactly_as_it_was() -> None:
    by_id = scored_by_id()

    assert not by_id["ledger"].lane_match and not by_id["petcam"].lane_match
    # With no lane the URL decides again, which is the pre-existing rule.
    assert _evidence_rank(by_id["ledger"]) < _evidence_rank(by_id["petcam"])


def test_lane_vocabulary_is_about_the_work_not_about_polish() -> None:
    backend = lane_profile(
        "A REST API over PostgreSQL with a Redis cache, deployed on Kubernetes."
    )
    assert backend.leader == "backend"

    testing = lane_profile(
        "Wrote a Selenium regression suite in pytest and fixed the flaky tests "
        "in the nightly test automation run."
    )
    assert testing.leader == "test"


def test_a_project_that_talks_about_everything_claims_no_lane() -> None:
    mixed = lane_profile(
        "A React frontend over a FastAPI service that trains a model and loads "
        "a Spark job."
    )
    assert mixed.leader is None, "one mention each is not a specialism"
