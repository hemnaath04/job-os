"""What the tailor works out before it writes anything.

The old flow drafted blind and then spent up to five more full model calls
rediscovering two things Python already knew: which requirements the JD is scored
on, and which of them the candidate's own vault already words. A measured run
took 579 seconds over five passes to climb 49 to 87 against a list
`_jd_requirements` derives in under a millisecond.

These cases pin the replacement: the rubric reaches the writer before the first
word, and a run stops as soon as nothing is left that another pass could honestly
fix.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from _fake_llm import StreamingFakeMessages
from job_os.schemas.resumes import GapQuestion, RequirementMatch, TailorAnalysis
from job_os.services import tailor
from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _analysis_block,
    _evidence_items,
    _jd_requirements,
    _project_relevance,
    _project_relevance_briefing,
    _reachable_missing,
    _requirement_briefing,
    _requirement_coverage,
)

JD = {
    "required_skills": ["Python", "FastAPI", "Kubernetes"],
    "preferred_skills": ["Terraform"],
}


def _profile() -> tuple[list[TailorFact], dict[str, list[TailorBullet]]]:
    facts = [
        TailorFact(id="skill-py", kind="skill", title="Python", org="Languages"),
        TailorFact(id="proj", kind="project", title="Job Searcher"),
    ]
    bullets = {
        "proj": [
            TailorBullet(
                id="b1",
                fact_id="proj",
                text="Built a FastAPI service that ranks postings against a resume.",
            )
        ]
    }
    return facts, bullets


def _coverage() -> dict[str, Any]:
    facts, bullets = _profile()
    requirements, _prose, _excluded = _jd_requirements(JD)
    return _requirement_coverage(requirements, _evidence_items(facts, bullets))


# A stand-in for the bug reported against production: an AI-engineer JD kept
# surfacing "BedRocked" (a hackathon 3D map) and "Infant Cry" (a health-audio
# side project) while dropping "job.os" (this very tailoring pipeline),
# "ClaimFarm" (an LLM claims-review agent) and "RoleReveal" (an LLM job-fit
# agent), the three projects that actually name this JD's own stack. Before
# `_project_relevance` existed, nothing in the pipeline measured that
# difference: both prompts said "strongest first" and left it to the model's
# own read of the raw facts list.
_AI_ENGINEER_JD = {
    "required_skills": ["Python", "FastAPI", "Anthropic", "Postgres"],
    "preferred_skills": ["retrieval"],
    "technologies": ["Claude"],
    "keywords": ["agent"],
}


def _project_facts() -> tuple[list[TailorFact], dict[str, list[TailorBullet]]]:
    facts = [
        TailorFact(id="jobos", kind="project", title="job.os"),
        TailorFact(id="rolereveal", kind="project", title="RoleReveal"),
        TailorFact(id="claimfarm", kind="project", title="ClaimFarm"),
        TailorFact(id="bedrocked", kind="project", title="BedRocked"),
        TailorFact(id="infantcry", kind="project", title="Infant Cry"),
    ]
    bullets = {
        "jobos": [
            TailorBullet(
                id="b-jobos",
                fact_id="jobos",
                text=(
                    "Built a FastAPI service using Claude and Anthropic APIs with "
                    "an agent pipeline over Postgres, adding retrieval to tailor "
                    "resumes."
                ),
            )
        ],
        "rolereveal": [
            TailorBullet(
                id="b-rolereveal",
                fact_id="rolereveal",
                text=(
                    "Built a Chrome extension scoring job postings with an agent "
                    "that calls Anthropic Claude."
                ),
            )
        ],
        "claimfarm": [
            TailorBullet(
                id="b-claimfarm",
                fact_id="claimfarm",
                text=(
                    "Built an LLM claims-review agent with retrieval over a "
                    "vector store."
                ),
            )
        ],
        "bedrocked": [
            TailorBullet(
                id="b-bedrocked",
                fact_id="bedrocked",
                text=(
                    "Built a 3D hackathon demo mapping sewer infrastructure with "
                    "React and MapLibre."
                ),
            )
        ],
        "infantcry": [
            TailorBullet(
                id="b-infantcry",
                fact_id="infantcry",
                text=(
                    "Built a health audio classifier for infant cry detection "
                    "using PyTorch."
                ),
            )
        ],
    }
    return facts, bullets


def test_project_relevance_ranks_by_jd_overlap_not_by_title_or_bullet_order() -> None:
    """The exact bug: nothing used to measure this, so nothing corrected it.

    job.os, RoleReveal and ClaimFarm all name this JD's stack in their own
    bullets; BedRocked and Infant Cry name none of it. A ranking that is not
    computed from JD overlap has no reason to put the first three ahead of the
    last two, and that is exactly what kept happening in production.
    """
    facts, bullets = _project_facts()
    requirements, _prose, _excluded = _jd_requirements(_AI_ENGINEER_JD)
    scored = _project_relevance(facts, bullets, requirements)

    by_title = {item.title: item.score for item in scored}
    assert by_title["job.os"] == 6
    assert by_title["RoleReveal"] == 3
    assert by_title["ClaimFarm"] == 2
    assert by_title["BedRocked"] == 0
    assert by_title["Infant Cry"] == 0

    # Every JD-relevant project outranks every irrelevant one.
    relevant = {"job.os", "RoleReveal", "ClaimFarm"}
    irrelevant = {"BedRocked", "Infant Cry"}
    assert min(by_title[t] for t in relevant) > max(by_title[t] for t in irrelevant)

    # Strongest first, ties broken by title so the order is reproducible.
    assert [item.title for item in scored] == [
        "job.os",
        "RoleReveal",
        "ClaimFarm",
        "BedRocked",
        "Infant Cry",
    ]


def test_project_relevance_briefing_tells_the_model_to_prefer_the_top_scores() -> None:
    facts, bullets = _project_facts()
    requirements, _prose, _excluded = _jd_requirements(_AI_ENGINEER_JD)
    scored = _project_relevance(facts, bullets, requirements)
    briefing = _project_relevance_briefing(scored)

    assert "PROJECT RELEVANCE" in briefing
    # The ranking is spelled out in order, not left for the model to re-derive.
    jobos_line = briefing.index("job.os")
    rolereveal_line = briefing.index("RoleReveal")
    claimfarm_line = briefing.index("ClaimFarm")
    bedrocked_line = briefing.index("BedRocked")
    infantcry_line = briefing.index("Infant Cry")
    assert jobos_line < rolereveal_line < claimfarm_line < bedrocked_line
    assert bedrocked_line < infantcry_line
    assert "Prefer the highest-scoring projects" in briefing
    assert "3 to 4 projects" in briefing
    assert "settling for fewer" in briefing


def test_project_relevance_briefing_is_silent_below_two_projects() -> None:
    """Ranking a field of zero or one settles nothing, so say nothing."""
    facts, bullets = _project_facts()
    requirements, _prose, _excluded = _jd_requirements(_AI_ENGINEER_JD)
    one_fact = [f for f in facts if f.title == "job.os"]
    scored = _project_relevance(one_fact, bullets, requirements)
    assert _project_relevance_briefing(scored) == ""


@pytest.mark.asyncio
async def test_the_compose_prompt_actually_carries_the_project_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the writer's own first message, not just the helper function.

    Pinning this at `run_tailor` is what proves the ranking is wired into the
    real pipeline and not just a helper nobody calls.
    """
    facts, bullets = _project_facts()
    written = {
        "selected_fact_ids": ["jobos", "rolereveal", "claimfarm"],
        "selected_bullets": [
            {
                "fact_bullet_id": "b-jobos",
                "rewritten_text": bullets["jobos"][0].text,
                "target_section": "projects",
            }
        ],
        "agent_note": "picked the three that match the JD",
    }
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            payload = {} if "analyst step" in kwargs["system"] else written
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    monkeypatch.setattr(tailor, "document_quality_flags", lambda _doc, **_kw: {})

    document, _prov, _gaps, _score, _report, _note = await tailor.run_tailor(
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume={"basics": {"name": "A Candidate"}},
        jd_parsed=_AI_ENGINEER_JD,
        jd_clean="Python, FastAPI, Anthropic, Postgres, Claude, agents, retrieval",
    )

    compose_calls = [c for c in calls if "analyst step" not in c["system"]]
    assert compose_calls, "the writer never ran"
    first_message = compose_calls[0]["messages"][0]["content"]
    assert "PROJECT RELEVANCE" in first_message
    assert first_message.index("job.os") < first_message.index("BedRocked")
    assert first_message.index("RoleReveal") < first_message.index("Infant Cry")

    # And the page actually filled with the projects the model, guided by that
    # ranking, chose: more than one, all of them JD-relevant.
    project_names = [p["name"] for p in document["projects"]]
    assert len(project_names) >= 2
    assert set(project_names) == {"job.os", "RoleReveal", "ClaimFarm"}


def test_a_skill_the_page_always_prints_is_already_met() -> None:
    """Skills render in full whichever bullets the writer picks.

    Sending the writer after a keyword the page prints anyway spends a bullet on
    work that is already done.
    """
    coverage = _coverage()
    assert coverage["Python"].free
    assert not coverage["Python"].selectable


def test_a_requirement_only_a_bullet_carries_is_a_selection_decision() -> None:
    coverage = _coverage()
    assert not coverage["FastAPI"].free
    assert coverage["FastAPI"].selectable == ("Job Searcher bullet b1",)


def test_a_requirement_absent_from_the_whole_vault_is_found_nowhere() -> None:
    assert not _coverage()["Kubernetes"].found


def test_the_briefing_tells_the_writer_which_bullet_carries_which_requirement() -> None:
    requirements, _prose, _excluded = _jd_requirements(JD)
    briefing = _requirement_briefing(requirements, _coverage())
    assert "ALREADY MET" in briefing
    assert "Python" in briefing
    assert "FastAPI  ->  Job Searcher bullet b1" in briefing
    assert "Kubernetes" in briefing
    # A nice-to-have is reported, and reported as not counting, so it is never
    # traded for a must-have.
    assert "Terraform" in briefing
    assert "NOT part of the number" in briefing
    # The list is a diagnostic. Handing over the scored terms without saying so
    # would be handing over a stuffing target.
    assert "not a checklist to satisfy" in briefing


def test_a_miss_the_vault_cannot_answer_is_not_worth_another_pass() -> None:
    """The stretch-role case, and the reason the old loop wasted minutes.

    A real point72 run held 55.6 across passes three, four and five: two full
    model calls proving to itself that the candidate has not done the work.
    """
    coverage = _coverage()
    reachable = _reachable_missing(
        ["FastAPI", "Kubernetes"], coverage=coverage, analysis=TailorAnalysis()
    )
    assert reachable == ["FastAPI"]


def test_the_analyst_can_make_a_miss_reachable() -> None:
    """A requirement worded nowhere can still be genuinely covered.

    Word matching cannot see that a retrieval system IS RAG, which is the one
    judgement the analyst call exists to make.
    """
    reachable = _reachable_missing(
        ["Kubernetes"],
        coverage=_coverage(),
        analysis=TailorAnalysis(
            covered=[RequirementMatch(requirement="kubernetes", fact_bullet_id="b1")]
        ),
    )
    assert reachable == ["Kubernetes"]


def test_the_plan_quotes_the_bullet_the_analyst_pointed_at() -> None:
    facts, bullets = _profile()
    block = _analysis_block(
        TailorAnalysis(
            positioning="Backend engineer who ships retrieval systems.",
            covered=[
                RequirementMatch(
                    requirement="RAG",
                    fact_bullet_id="b1",
                    rename="call the ranking pipeline a RAG pipeline",
                )
            ],
            gaps=[GapQuestion(requirement="Kubernetes", why_no_match="never used it")],
            shortlist_fact_ids=["proj"],
        ),
        bullets_by_id={b.id: b for bs in bullets.values() for b in bs},
        facts_by_id={f.id: f for f in facts},
    )
    assert "Built a FastAPI service" in block
    assert "call the ranking pipeline a RAG pipeline" in block
    assert "Kubernetes: never used it" in block
    assert "Job Searcher" in block


@pytest.mark.asyncio
async def test_one_writing_pass_when_nothing_is_left_to_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean draft against an unreachable requirement ends the run.

    Every remaining miss is work the candidate has not done and the writing is
    clean, so a second pass could only reword a page that is already right. That
    is the four minutes the old loop spent on every stretch role.
    """
    facts, bullets = _profile()
    written = {
        "selected_fact_ids": ["proj"],
        "selected_bullets": [
            {
                "fact_bullet_id": "b1",
                "rewritten_text": (
                    "Built a FastAPI service that ranks postings against a resume."
                ),
                "target_section": "projects",
            }
        ],
        "ats_keywords_matched": ["Python", "FastAPI"],
        "ats_keywords_missing": ["Kubernetes"],
        "agent_note": "one pass",
    }
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            payload: dict[str, Any] = (
                {"gaps": [{"requirement": "Kubernetes", "why_no_match": "no evidence"}]}
                if "analyst step" in kwargs["system"]
                else written
            )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    # The page is short by design here, and a thin-page flag would be a genuine
    # reason to run a second pass. Only the reachability rule is under test.
    monkeypatch.setattr(tailor, "document_quality_flags", lambda _doc, **_kw: {})

    stages: list[tailor.TailorStage] = []
    _doc, _prov, _gaps, score, report, note = await tailor.run_tailor(
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume={"basics": {"name": "A Candidate"}},
        jd_parsed=JD,
        jd_clean="Python, FastAPI and Kubernetes",
        on_progress=stages.append,
    )

    systems = [call["system"] for call in calls]
    assert sum(1 for system in systems if "analyst step" in system) == 1
    assert sum(1 for system in systems if "analyst step" not in system) == 1
    assert report["iterations"] == [float(score)]
    # The one miss is named as the candidate's to close rather than reported as a
    # number the tool failed to reach.
    assert report["missing_needs_new_facts"] == ["Kubernetes"]
    assert "another pass cannot close it" in note
    # Every step the user was shown maps to work that actually ran.
    assert [stage.step for stage in stages] == [
        "read_role",
        "match_evidence",
        "find_gaps",
        "find_gaps",
        "compose",
        "check_claims",
        "assemble",
    ]
    assert stages[1].detail == "2 of 3 already backed by your profile"


@pytest.mark.asyncio
async def test_an_unchecked_gap_is_not_proof_that_a_pass_would_be_wasted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An analyst that never answered has not proved anything.

    Word matching cannot see that a retrieval system IS RAG, so with no analysis
    "unreachable" only means "unchecked". A real run whose analyst reply was
    truncated mid-object stopped one pass early at Job Match 61, where the same
    flow with a working analysis reached 78.
    """
    facts, bullets = _profile()
    written = {
        "selected_bullets": [
            {
                "fact_bullet_id": "b1",
                "rewritten_text": "Built a FastAPI service that ranks postings.",
                "target_section": "projects",
            }
        ],
        "agent_note": "one pass",
    }
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            if "analyst step" in kwargs["system"]:
                # Truncated exactly as the real one was.
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text='{"covered": [{"req')]
                )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(written))]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    monkeypatch.setattr(tailor, "document_quality_flags", lambda _doc, **_kw: {})

    _doc, _prov, _gaps, _score, report, _note = await tailor.run_tailor(
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume={"basics": {"name": "A Candidate"}},
        jd_parsed=JD,
        jd_clean="Python, FastAPI and Kubernetes",
    )
    # The writer got a second go rather than the run declaring the job impossible
    # on the strength of a reply it never read.
    assert len(report["iterations"]) > 1


@pytest.mark.asyncio
async def test_an_analyst_bullet_id_that_does_not_exist_never_reaches_the_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invented id would send the writer hunting for evidence that is not there."""

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text",
                        text=json.dumps(
                            {
                                "covered": [
                                    {"requirement": "RAG", "fact_bullet_id": "b1"},
                                    {
                                        "requirement": "Kubernetes",
                                        "fact_bullet_id": "made-up",
                                    },
                                ]
                            }
                        ),
                    )
                ]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    requirements, _prose, _excluded = _jd_requirements(JD)
    analysis = await tailor._analyse_requirements(
        FakeAnthropic(),
        model="manifest/auto",
        tier="job-os-sonnet",
        jd_parsed=JD,
        jd_clean="",
        facts_payload=[],
        unresolved=requirements,
        valid_bullet_ids={"b1"},
    )
    assert [match.fact_bullet_id for match in analysis.covered] == ["b1"]


@pytest.mark.asyncio
async def test_a_failed_analyst_call_does_not_sink_the_run() -> None:
    """The deterministic briefing already carries most of what the writer needs."""

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **_kwargs: Any) -> Any:
            raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    analysis = await tailor._analyse_requirements(
        FakeAnthropic(),
        model="manifest/auto",
        tier="job-os-sonnet",
        jd_parsed=JD,
        jd_clean="",
        facts_payload=[],
        unresolved=_jd_requirements(JD)[0],
        valid_bullet_ids=set(),
    )
    assert analysis == TailorAnalysis()
