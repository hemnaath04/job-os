"""Skill rows a reader would not call keyword stuffing.

The independent review flagged the real output for listing "RAG" beside
"Retrieval-Augmented Generation (RAG)" and "LLM Integration" beside "LLM
integration (OpenAI, Anthropic, Qwen)".
"""
from __future__ import annotations

from job_os.services.tailor import _consolidate_skills, _skill_aliases


def test_an_acronym_and_its_expansion_are_one_skill() -> None:
    groups = _consolidate_skills(
        {"AI / ML": ["RAG", "Retrieval-Augmented Generation (RAG)"]}
    )
    # The fuller spelling wins the slot: it says everything the acronym says.
    assert groups[0]["keywords"] == ["Retrieval-Augmented Generation (RAG)"]


def test_a_parenthetical_list_does_not_split_a_skill_in_two() -> None:
    groups = _consolidate_skills(
        {"AI / ML": ["LLM Integration", "LLM integration (OpenAI, Anthropic, Qwen)"]}
    )
    assert groups[0]["keywords"] == ["LLM integration (OpenAI, Anthropic, Qwen)"]


def test_a_qualified_skill_is_not_folded_into_the_bare_one() -> None:
    """"Async Python" and "Python" are two claims and both belong.

    Matching on token containment would have collapsed them, which is why the
    alias rule is restricted to parentheticals.
    """
    groups = _consolidate_skills({"Backend": ["Python", "Async Python"]})
    assert groups[0]["keywords"] == ["Python", "Async Python"]


def test_agent_drop_list_removes_a_vendor_named_in_two_rows() -> None:
    """The alias matcher above deliberately does not do this (see
    test_aliases_stay_narrow): "OpenAI" inside a parenthetical provider list
    is not the same claim as the skill it lists providers for, so it will not
    fold "OpenAI / Anthropic SDKs" into "LLM integration (OpenAI, Anthropic,
    Qwen)" on its own. That is what skills_dedup_drop is for: the compose
    agent sees both and names the redundant one for removal.
    """
    groups = _consolidate_skills(
        {
            "AI / ML": [
                "LLM integration (OpenAI, Anthropic, Qwen)",
                "OpenAI / Anthropic SDKs",
            ]
        },
        drop=["OpenAI / Anthropic SDKs"],
    )
    assert groups[0]["keywords"] == ["LLM integration (OpenAI, Anthropic, Qwen)"]


def test_a_drop_string_that_matches_nothing_real_drops_nothing() -> None:
    groups = _consolidate_skills(
        {"AI / ML": ["LLM integration (OpenAI, Anthropic, Qwen)"]},
        drop=["a skill the candidate never listed"],
    )
    assert groups[0]["keywords"] == ["LLM integration (OpenAI, Anthropic, Qwen)"]


def test_aliases_stay_narrow() -> None:
    assert _skill_aliases("Retrieval-Augmented Generation (RAG)") == {
        "retrieval augmented generation rag",
        "retrieval augmented generation",
        "rag",
    }
    # A list of providers names the providers, not the skill.
    assert "openai anthropic qwen" not in _skill_aliases(
        "LLM integration (OpenAI, Anthropic, Qwen)"
    )
    assert _skill_aliases("Python") == {"python"}


# --- a drop has to be earned -------------------------------------------------
#
# `skills_dedup_drop` was applied on trust. A real run printed
# "Backend & Data: REST APIs, Spatial Joins" and "Infra: Vercel, Autodesk" for a
# candidate whose vault holds FastAPI, Docker, PostgreSQL, Async Python, Pytest,
# Selenium, Embeddings and the Anthropic SDKs. Nothing checked whether the page
# already said the thing being removed.
#
# The pressure is real and worth recording: `unevidenced_skill` tells the writer
# to drop any skill no bullet demonstrates, and one page cannot demonstrate
# forty. So the drop list is where a good instruction turns into a gutted resume.

HIS_VAULT = {
    "Languages": ["Python", "Java", "Go", "SQL", "Bash"],
    "Backend & Data": ["FastAPI", "REST APIs", "Async Python", "PostgreSQL", "Spatial Joins"],
    "Testing & CI/CD": ["Pytest", "Selenium", "GitHub Actions"],
    "Infrastructure & Docs": ["Docker", "Linux", "Vercel", "Git"],
}


def test_a_drop_the_page_does_not_already_say_is_refused() -> None:
    # The shape of the real failure: most of the block named for removal.
    groups = _consolidate_skills(
        HIS_VAULT,
        drop=["FastAPI", "Async Python", "PostgreSQL", "Pytest", "Docker"],
    )
    kept = {k for g in groups for k in g["keywords"]}

    for skill in ("FastAPI", "Async Python", "PostgreSQL", "Pytest", "Docker"):
        assert skill in kept, f"{skill} is not said anywhere else on the page"


def test_a_broader_skill_goes_when_a_narrower_one_already_names_it() -> None:
    # "Python" against a block that also lists "Async Python" is the one case
    # here where a drop of a bare technology name is honoured, and it should be:
    # the page still says the word, so nothing a reader or a keyword scan looks
    # for has gone. Recorded as a test because it looks wrong at a glance and is
    # not, and the next person to see "Python" disappear deserves the reason.
    groups = _consolidate_skills(
        {"Backend & Data": ["Async Python", "Python", "FastAPI"]},
        drop=["Python"],
    )
    kept = [k for g in groups for k in g["keywords"]]

    assert "Python" not in kept
    assert "Async Python" in kept


def test_a_genuinely_redundant_drop_is_still_honoured() -> None:
    # The case the feature exists for keeps working: the vendor names are
    # already inside the longer entry, so the bare one goes.
    groups = _consolidate_skills(
        {"AI / ML": ["LLM integration (OpenAI, Anthropic, Qwen)", "OpenAI / Anthropic SDKs"]},
        drop=["OpenAI / Anthropic SDKs"],
    )
    assert groups[0]["keywords"] == ["LLM integration (OpenAI, Anthropic, Qwen)"]


def test_filler_words_do_not_block_an_otherwise_redundant_drop() -> None:
    # "SDKs" is not the part of "OpenAI / Anthropic SDKs" that carries a claim,
    # so its absence from the longer entry should not save the shorter one.
    groups = _consolidate_skills(
        {"AI / ML": ["Anthropic Claude models", "Anthropic SDKs"]},
        drop=["Anthropic SDKs"],
    )
    assert groups[0]["keywords"] == ["Anthropic Claude models"]


def test_defensible_drops_still_cannot_empty_the_block() -> None:
    # Each drop below is individually redundant, and together they would print a
    # skills block for somebody else.
    vault = {"Languages": ["Python", "Python 3", "Python 3.12"] + [f"Skill {i}" for i in range(6)]}
    groups = _consolidate_skills(vault, drop=[f"Skill {i}" for i in range(6)] + ["Python"])
    kept = {k for g in groups for k in g["keywords"]}

    assert len(kept) >= 8, f"floor breached: {sorted(kept)}"


def test_a_short_vault_prints_what_it_has() -> None:
    # The floor protects a long list from being gutted. It does not invent a
    # longer one for a candidate who has four skills.
    groups = _consolidate_skills(
        {"AI / ML": ["LLM integration (OpenAI, Anthropic, Qwen)", "OpenAI / Anthropic SDKs"]},
        drop=["OpenAI / Anthropic SDKs"],
    )
    assert groups[0]["keywords"] == ["LLM integration (OpenAI, Anthropic, Qwen)"]
