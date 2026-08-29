"""The skills row answers the posting it was written for.

A posting opened by asking for "Experience with C/C++/Java/Go/Python". The
candidate writes two of those five. The printed row led with SQL and Bash,
because that is the order the vault happened to hold, and the reader had to go
looking for the answer to the posting's first question.

Ordering is the whole fix, and ordering is all of it. Nothing here adds a
language, so a posting asking for C++ against a profile without it changes
nothing at all: that stays a gap, which is the honest outcome.

Fixtures are a generic candidate. Nothing here should depend on any one
person's stack.
"""
from __future__ import annotations

from job_os.services.tailor import (
    _consolidate_skills,
    _order_skills_by_jd,
    jd_skill_order,
)

# The posting shape from the repro: a slash-joined any-of list first, then the
# rest of the stack, then a nice-to-have.
POSTING = {
    "required_skills": [
        "Experience with C/C++/Java/Go/Python",
        "distributed systems",
    ],
    "technologies": ["Kubernetes", "PostgreSQL"],
    "preferred_skills": ["machine learning"],
}

# A candidate who writes two of the five languages and nothing exotic.
VAULT = {
    "Languages": ["SQL", "Bash", "Python", "Java"],
    "Infrastructure": ["Docker", "Kubernetes", "PostgreSQL"],
    "Data": ["Pandas", "Machine Learning"],
}


def ordered(vault: dict[str, list[str]], posting: dict) -> list[dict]:
    return _order_skills_by_jd(_consolidate_skills(vault), jd_skill_order(posting))


def test_a_slash_list_contributes_its_members_in_the_postings_order() -> None:
    order = jd_skill_order(POSTING)
    positions = {term: index for index, term in enumerate(order)}

    assert positions["Java"] < positions["Go"] < positions["Python"], (
        "the posting wrote them in that order and that is the order that counts"
    )


def test_the_row_leads_with_the_languages_the_posting_named() -> None:
    languages = next(g for g in ordered(VAULT, POSTING) if g["name"] == "Languages")

    assert languages["keywords"] == ["Java", "Python", "SQL", "Bash"]


def test_a_language_the_candidate_does_not_have_is_never_added() -> None:
    printed = {k for group in ordered(VAULT, POSTING) for k in group["keywords"]}

    assert "C++" not in printed and "Go" not in printed, (
        "the posting asking for it is not evidence the candidate writes it"
    )


def test_nothing_is_dropped_for_going_unmentioned() -> None:
    before = {k for group in _consolidate_skills(VAULT) for k in group["keywords"]}
    after = {k for group in ordered(VAULT, POSTING) for k in group["keywords"]}

    assert before == after, "this reorders a row, it does not curate one"


def test_rows_are_ordered_by_how_early_the_posting_asks_for_them() -> None:
    names = [group["name"] for group in ordered(VAULT, POSTING)]

    # Languages open the posting, the infrastructure list follows, and the
    # nice-to-have trails.
    assert names == ["Languages", "Infrastructure", "Data"]


def test_within_a_row_the_postings_order_beats_the_vaults() -> None:
    infra = next(g for g in ordered(VAULT, POSTING) if g["name"] == "Infrastructure")

    assert infra["keywords"] == ["Kubernetes", "PostgreSQL", "Docker"]


def test_a_qualified_skill_still_answers_the_bare_ask() -> None:
    vault = {"Backend": ["Async Python", "Flask"]}
    posting = {"required_skills": ["Python"]}
    groups = ordered(vault, posting)

    assert groups[0]["keywords"] == ["Async Python", "Flask"]


def test_a_short_language_does_not_claim_a_word_that_merely_contains_it() -> None:
    """"Go" inside "MongoDB" is the reason `_mentions` exists. It applies here too."""
    vault = {"Data": ["MongoDB", "Django"]}
    posting = {"required_skills": ["Go"]}
    groups = ordered(vault, posting)

    assert groups[0]["keywords"] == ["MongoDB", "Django"], "no match, so no reordering"


def test_a_posting_that_named_nothing_leaves_the_row_exactly_as_it_was() -> None:
    groups = ordered(VAULT, {})

    assert [g["keywords"] for g in groups] == [
        g["keywords"] for g in _consolidate_skills(VAULT)
    ]


def test_the_unnamed_row_stays_last_even_when_the_posting_asks_for_it() -> None:
    """`Additional` is where skills with no category of their own land.

    It is last because a reader who reaches it has already read the rows that
    told them something, and a keyword match is not a reason to promote a row
    with no heading.
    """
    vault = {
        "Languages": ["Ruby"],
        "Skills": ["Kubernetes"],
    }
    names = [group["name"] for group in ordered(vault, POSTING)]

    assert names[-1] == "Additional"


def test_a_prose_requirement_still_contributes_its_order() -> None:
    """A skill named inside a sentence orders the row like any other.

    Recovery is the scorer's own `_skills_inside_prose`, deliberately, so the
    row is ordered by the same reading of the posting the Keyword Match number
    is built from. It does not recover everything -- here the clause carrying
    PostgreSQL is too long to be a skill on its own -- and a skill it could not
    name simply keeps its place further down the row rather than being promoted
    on a guess.
    """
    posting = {
        "qualifications": [
            "Currently pursuing a degree and comfortable working with "
            "PostgreSQL, Redis and Kubernetes"
        ]
    }
    vault = {"Infrastructure": ["PostgreSQL", "Kubernetes", "Redis"]}
    groups = ordered(vault, posting)

    assert groups[0]["keywords"] == ["Redis", "Kubernetes", "PostgreSQL"]


def test_a_projects_technologies_follow_the_posting_too() -> None:
    """The render caps a tech line at six. This decides which six.

    Without an order the six kept are the six that happen to sit first in the
    vault, which answers no posting in particular. BedRocked's twelve, taken
    verbatim from the vault, against a posting written in computer-vision
    nouns: the ones it asks for have to come off the front.
    """
    from job_os.services.tailor import _order_keywords_by_jd

    vault = [
        "Python", "FastAPI", "scikit-learn", "Anthropic Claude",
        "Autodesk APS", "Vercel", "Knowledge Distillation",
        "Computer Vision", "LLM Integration", "Generative AI",
        "Model Inference", "Classification",
    ]
    posting = jd_skill_order(
        {
            "required_skills": ["Computer Vision", "Model Inference"],
            "technologies": ["Classification", "Python"],
        }
    )

    ordered = _order_keywords_by_jd(vault, posting)

    assert set(ordered) == set(vault), "this is a reorder, nothing added or dropped"
    kept = ordered[:6]
    for wanted in ("Computer Vision", "Model Inference", "Classification", "Python"):
        assert wanted in kept, f"{wanted} was asked for and did not survive the cut"


def test_ordering_keywords_without_a_posting_changes_nothing() -> None:
    """No posting is not an excuse to reshuffle somebody's own ordering."""
    from job_os.services.tailor import _order_keywords_by_jd

    vault = ["Vercel", "Python", "FastAPI"]
    assert _order_keywords_by_jd(vault, None) == vault
    assert _order_keywords_by_jd(vault, []) == vault
