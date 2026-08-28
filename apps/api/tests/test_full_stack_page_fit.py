"""A full-stack posting is hiring for two kinds of work, and the page shows both.

Lanes were built to stop a one-line mention of another discipline from
reordering projects (see test_project_lane_ranking.py). They read a posting as
ONE kind of work, which is right for a platform job and wrong for a full-stack
one, and the wrongness had a direction:

  * `LANE_TERMS["backend"]` holds twice as many words as `LANE_TERMS["frontend"]`,
    so a posting naming both sides evenly still counts more backend hits.
  * `_FUNCTION_LANES` mapped `function: "swe"` -- the commonest value in the
    whole index, and the one that says the least -- to backend, worth four
    extra hits.

Together those turned "React and TypeScript on the front end, Python services
behind them" into a backend role. The product/UI project then had
`lane_match=False` against a backend project's True, and `_evidence_rank` puts
the lane first, so the UI project was the one cut from the page even when it
matched exactly as many requirements.

The fix is not a bigger frontend word list. It is that a posting can be about
two lanes, and a project in either is the same kind of work as the job. The
tie then falls to evidence that is not lane-shaped at all: a reachable URL,
still being worked on, how recently it started.

Fixtures are a generic candidate with a generic stack.
"""
from __future__ import annotations

from datetime import date

from job_os.services.role_lane import jd_lane, jd_lanes, text_lanes
from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _evidence_rank,
    _project_relevance,
    _Requirement,
)

# A real full-stack shape: the employer names both halves, in one breath.
FULL_STACK_POSTING = {
    "title": "Full Stack Engineer",
    "function": "swe",
    "required_skills": ["TypeScript", "React", "Python", "PostgreSQL", "REST APIs"],
    "technologies": ["Next.js", "FastAPI", "Docker"],
    "responsibilities": [
        "Build the customer-facing screens people use every day",
        "Design and ship the services behind them",
    ],
}
FULL_STACK_JD = (
    "You will work across the whole product: React and TypeScript on the front "
    "end, Python services and PostgreSQL behind them. You will own "
    "customer-facing screens, the design system components they use, "
    "accessibility, and the REST APIs and Docker deployments that serve them."
)

# Two projects that answer the SAME number of this posting's requirements, one
# from each side of it. That equality is the whole point of the fixture.
UI_PROJECT = TailorFact(
    id="storefront",
    kind="project",
    title="Storefront",
    start_date=date(2025, 4, 1),
    payload={
        "description": (
            "A React and TypeScript storefront with a shared component library. "
            "Responsive design, accessibility audited, built on Next.js."
        )
    },
)
SERVICE_PROJECT = TailorFact(
    id="ledger",
    kind="project",
    title="Ledger Sync",
    start_date=date(2025, 4, 1),
    source_url="https://example.com/ledger",
    payload={
        "description": (
            "A Python service reconciling bank feeds. REST API over PostgreSQL, "
            "a queue consumer, deployed in Docker containers."
        )
    },
)
BULLETS: dict[str, list[TailorBullet]] = {
    "storefront": [
        TailorBullet(id="b1", fact_id="storefront", text="Built the checkout screens.")
    ],
    "ledger": [
        TailorBullet(id="b2", fact_id="ledger", text="Wrote the reconciliation service.")
    ],
}

# One requirement each, so neither project can win on the count.
TIED_REQUIREMENTS = [
    _Requirement(label="React", alternatives=("React",), preferred=False),
    _Requirement(label="Python", alternatives=("Python",), preferred=False),
]


# --- reading the posting ------------------------------------------------------


def test_a_full_stack_posting_is_read_as_both_kinds_of_work() -> None:
    lanes = jd_lanes(FULL_STACK_POSTING, FULL_STACK_JD)

    assert set(lanes) == {"backend", "frontend"}


def test_a_full_stack_posting_names_no_single_lane() -> None:
    """`jd_lane` is the honest single-name reading, and there is no honest
    single name for this posting. Callers that need to rank use `jd_lanes`."""
    assert jd_lane(FULL_STACK_POSTING, FULL_STACK_JD) is None


def test_software_engineer_alone_does_not_make_a_posting_backend() -> None:
    """`function: "swe"` says the role is engineering, not which half of it."""
    posting = {"title": "Software Engineer", "function": "swe"}
    jd = "Join the product team. You will build things users touch."

    assert jd_lanes(posting, jd) == ()


def test_a_posting_that_genuinely_commits_still_reads_as_one_lane() -> None:
    """The guardrail. Widening this must not make every posting full-stack."""
    posting = {
        "title": "Backend Engineer, Serving Platform",
        "function": "swe",
        "required_skills": ["Python", "Kubernetes", "distributed systems"],
        "technologies": ["PostgreSQL", "computer vision"],
    }
    jd = (
        "Build and operate the backend services behind our model serving "
        "platform: REST APIs, queue consumers, PostgreSQL schemas and the "
        "Kubernetes deployments they run on. Latency and throughput are the "
        "job. Some traffic is computer vision inference, so exposure there is "
        "a plus."
    )

    assert jd_lanes(posting, jd) == ("backend",)


def test_a_project_is_read_the_same_way_the_posting_is() -> None:
    ui = text_lanes(
        "A React and TypeScript storefront with a shared component library, "
        "responsive design and an accessibility audit."
    )
    service = text_lanes(
        "A Python REST API over PostgreSQL with a Redis cache, deployed on "
        "Kubernetes."
    )

    assert "frontend" in ui and "backend" not in ui
    assert "backend" in service and "frontend" not in service


# --- ranking projects ---------------------------------------------------------


def test_the_ui_project_is_the_same_kind_of_work_as_a_full_stack_role() -> None:
    """The exact repro: this used to be False while the service project was True."""
    lanes = jd_lanes(FULL_STACK_POSTING, FULL_STACK_JD)
    scored = _project_relevance(
        [UI_PROJECT, SERVICE_PROJECT], BULLETS, TIED_REQUIREMENTS, lanes=lanes
    )
    by_id = {item.fact_id: item for item in scored}

    assert by_id["storefront"].lane_match
    assert by_id["ledger"].lane_match


def test_neither_side_is_ranked_below_the_other_for_being_its_side() -> None:
    """Both match one requirement, so the lane must separate nothing here.

    The tie is then broken by evidence that has no lane in it -- the service
    project has a recorded URL and the UI one does not -- which is a fair
    reason to prefer it and a different reason from "backend is the real work".
    """
    lanes = jd_lanes(FULL_STACK_POSTING, FULL_STACK_JD)
    scored = _project_relevance(
        [UI_PROJECT, SERVICE_PROJECT], BULLETS, TIED_REQUIREMENTS, lanes=lanes
    )
    by_id = {item.fact_id: item for item in scored}

    assert by_id["storefront"].score == by_id["ledger"].score == 1
    ui_rank = _evidence_rank(by_id["storefront"])
    service_rank = _evidence_rank(by_id["ledger"])
    # `_evidence_rank` reads worst-first and its leading element is the lane.
    # Equal there is the fix: the lane no longer decides this pair at all.
    assert ui_rank[0] == service_rank[0] == 1


def test_the_ui_project_wins_the_tie_when_its_own_evidence_is_stronger() -> None:
    """With the lane no longer tipping the scale, the better-evidenced project
    goes on the page whichever half of the stack it came from."""
    ui_with_url = UI_PROJECT.__class__(
        id="storefront",
        kind="project",
        title="Storefront",
        start_date=date(2025, 4, 1),
        source_url="https://example.com/storefront",
        payload=UI_PROJECT.payload,
    )
    older_service = SERVICE_PROJECT.__class__(
        id="ledger",
        kind="project",
        title="Ledger Sync",
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 1),
        payload=SERVICE_PROJECT.payload,
    )
    lanes = jd_lanes(FULL_STACK_POSTING, FULL_STACK_JD)
    scored = _project_relevance(
        [older_service, ui_with_url], BULLETS, TIED_REQUIREMENTS, lanes=lanes
    )

    assert scored[0].score == scored[1].score, "still a tie on the count"
    assert scored[0].fact_id == "storefront"


def test_a_lane_still_never_beats_a_higher_requirement_count() -> None:
    """The boundary that made lanes safe in the first place, unchanged."""
    heavier = [
        *TIED_REQUIREMENTS,
        _Requirement(label="PostgreSQL", alternatives=("PostgreSQL",), preferred=False),
        _Requirement(label="Docker", alternatives=("Docker",), preferred=False),
    ]
    scored = _project_relevance(
        [UI_PROJECT, SERVICE_PROJECT], BULLETS, heavier, lanes=("frontend",)
    )

    assert scored[0].fact_id == "ledger"
    assert scored[0].score > scored[1].score
