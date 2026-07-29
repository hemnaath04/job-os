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
