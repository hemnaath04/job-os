"""Terms a resume can never match must not count as must-haves.

The score is `met / total`, so anything in the denominator that no resume
could ever satisfy deflates it permanently and, worse, feeds the repair pass a
list of gaps to chase that no rewrite can close.

Every case here came off one real run: an AMD ML/AI co-op posting on
2026-08-28, which scored 9 of 27 must-haves. Four of those 27 were the posting
describing itself rather than asking for anything. The run's own gap questions
already knew, saying "role-type keyword, not a skill to match" and "employer
name from posting, not candidate experience", and scored them as misses anyway.
"""
from __future__ import annotations

import pytest

from job_os.services.tailor import _jd_requirements

# The keywords list from that posting, verbatim.
AMD_KEYWORDS = [
    "Machine Learning",
    "Artificial Intelligence",
    "AI",
    "ML",
    "Intern",
    "Co-op",
    "AMD",
    "Computer Vision",
    "Data Science",
    "Cloud",
    "Deep Learning",
]


def _required(jd: dict) -> list[str]:
    requirements, _prose, _excluded = _jd_requirements(jd)
    return [req.label for req in requirements if not req.preferred]


@pytest.mark.parametrize("term", ["Intern", "Co-op"])
def test_the_role_type_is_not_a_skill_to_match(term: str) -> None:
    """`internships?` caught the noun but not the bare word a keywords list
    carries, so an internship posting scored "Intern" as a skill the candidate
    had failed to demonstrate.

    Fed in through `technologies` rather than `keywords`. `keywords` is scored
    as a nice-to-have now, so asserting against it here would pass because the
    field is not scored at all rather than because the filter works, and a
    vacuous test is worse than no test. The filter is the thing under test.
    """
    assert term not in _required({"technologies": AMD_KEYWORDS})


def test_parse_debris_is_not_a_requirement() -> None:
    """Splitting "Familiarity with cloud (e.g., AWS, GCP, Azure)" on its
    punctuation leaves "e.g" behind as its own must-have."""
    jd = {"required_skills": ["Familiarity with cloud (e.g., AWS, GCP, Azure)"]}
    labels = _required(jd)
    assert "e.g" not in labels
    assert not any(label.strip(". ").lower() in ("e.g", "i.e", "etc") for label in labels)
    # The real skills inside that sentence still have to survive the cleanup.
    joined = " ".join(labels)
    assert "AWS" in joined and "GCP" in joined and "Azure" in joined


def test_the_real_skills_in_that_posting_still_count() -> None:
    """The guard must not eat the requirements the posting actually makes."""
    labels = " ".join(_required({"technologies": AMD_KEYWORDS}))
    for kept in ("Machine Learning", "Computer Vision", "Deep Learning"):
        assert kept in labels


def test_internal_is_not_mistaken_for_intern() -> None:
    # Word-boundary matched, the same care `firm`/`firmware` already gets.
    assert "Internal Tooling" in _required({"technologies": ["Internal Tooling"]})


# ---------------------------------------------------------------------------
# Junk recovered out of prose sentences
#
# Every sentence below is verbatim from a real posting in this workspace, and
# every fragment named was being scored as a must-have the candidate had failed
# to have. Grouped by what is wrong with the fragment rather than listed as a
# blocklist, because the fixes are rules about classes of fragment and a test
# per class is what stops the next posting needing a new rule.
# ---------------------------------------------------------------------------


def test_where_the_work_happens_is_not_a_skill() -> None:
    """"Available to work in person from our Union Square, NYC office".

    That yielded "NYC office" as a must-have. The whole sentence is set aside
    the way an enrollment rule is, rather than having a fragment mined out.
    """
    entry = "Available to work in person from our Union Square, NYC office"
    requirements, _prose, excluded = _jd_requirements({"qualifications": [entry]})
    assert [r.label for r in requirements] == []
    assert excluded == [entry]


def test_a_hybrid_or_on_site_technology_is_not_mistaken_for_a_location() -> None:
    """The location gate discards a whole sentence, so it has to stay narrow.

    "hybrid" and a bare "on-site" are the words that tempt an over-broad rule,
    and both begin real requirements. A false positive here deletes skills.
    """
    jd = {"required_skills": ["Experience with hybrid cloud, on-site data centres, and AWS"]}
    requirements, _prose, excluded = _jd_requirements(jd)
    # The sentence is scored rather than discarded, which is the thing at risk:
    # the location gate throws away a whole entry, so "hybrid" or a bare
    # "on-site" inside it would have cost every skill the sentence names.
    assert excluded == []
    assert "AWS" in " ".join(r.label for r in requirements)


@pytest.mark.parametrize(
    ("sentence", "junk"),
    [
        (
            "Hands-on experience or strong curiosity with AI-assisted development "
            "tools (e.g., GitHub Copilot, Cursor, Claude Code)",
            "Hands-on",
        ),
        (
            "Strong understanding of CS fundamentals and practical coding application",
            "practical coding application",
        ),
        (
            "Clear, direct communicator who can explain technical tradeoffs to "
            "both engineers and non-engineers",
            "Clear",
        ),
    ],
)
def test_an_evaluative_qualifier_is_not_a_skill(sentence: str, junk: str) -> None:
    """The posting grading a skill, rather than naming one."""
    assert junk not in _required({"required_skills": [sentence]})


def test_deep_learning_survives_the_qualifier_rule() -> None:
    """The rule matches at the start of a fragment, so "deep" cannot be in it.

    This is the false positive that would cost a real skill rather than leave
    noise behind, which is why the qualifier list is the length it is.
    """
    jd = {"required_skills": ["Experience with deep learning, computer vision, or NLP"]}
    assert "deep learning" in " ".join(_required(jd)).casefold()


def test_a_manner_adverb_marks_a_clause_rather_than_a_skill() -> None:
    """"...leveraging AI to learn across the stack, ramp up quickly, and
    contribute meaningfully from day one" was scoring "ramp up quickly"."""
    jd = {
        "required_skills": [
            "A budding AI-first mindset, comfortable experimenting with prompting "
            "techniques and leveraging AI to learn across the stack, ramp up "
            "quickly, and contribute meaningfully from day one"
        ]
    }
    assert "ramp up quickly" not in _required(jd)


def test_a_bare_verb_is_not_a_skill_but_the_gerund_still_is() -> None:
    """"Use AI to learn faster, debug, and ship" left the verb "debug" behind.

    The gerund is deliberately untouched: "debugging" is a word somebody writes
    on a resume, and `debug\\b` does not reach it.
    """
    assert "debug" not in _required(
        {"required_skills": ["Use AI to learn faster, debug, and ship"]}
    )
    assert "debugging" in " ".join(_required({"technologies": ["debugging"]}))


def test_the_audience_you_explain_things_to_is_not_a_skill() -> None:
    """"...explain technical tradeoffs to both engineers and non-engineers"."""
    jd = {
        "qualifications": [
            "Clear, direct communicator who can explain technical tradeoffs to "
            "both engineers and non-engineers"
        ]
    }
    assert "non-engineers" not in _required(jd)


def test_stakeholder_management_is_still_a_skill() -> None:
    """The counterpart-noun rule names only the negated forms, on purpose.

    Bare "stakeholders" stays out of it: stakeholder management is a real thing
    a resume claims, and excluding the word would delete it.
    """
    assert "stakeholder management" in " ".join(
        _required({"technologies": ["stakeholder management"]})
    )
