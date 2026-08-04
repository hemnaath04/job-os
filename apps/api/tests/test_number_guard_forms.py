"""Phase 7 attack 1: "unverified numbers are stripped".

The guard is `NUMBER_RE.findall(new) - NUMBER_RE.findall(source)`, so the regex is
the entire attack surface. This mirrors the real pipeline at
resume_engine.py:1051-1073 exactly: extract from the *source* (current doc +
verified facts + github context), extract from the model's output, subtract, and
call `_strip_unverified_numbers` with the difference.

The fixture is the brief's: one employer, two technologies, and NO metrics at all.
Any number that survives is a number the page now asserts and nothing supports.

    cd apps/api && .venv/bin/python -m pytest tests/test_number_guard_forms.py -v
"""
from __future__ import annotations

import json

import pytest

from job_os.services.resume_engine import (
    NUMBER_RE,
    _resume_text,
    _strip_unverified_numbers,
)

# One employer, two technologies, zero metrics.
SOURCE_DOC: dict = {
    "basics": {"name": "A Candidate", "email": "a@example.com", "summary": "Test engineer."},
    "work": [
        {
            "name": "Acme",
            "position": "Test Automation Engineer",
            "startDate": "2021-01",
            "endDate": "2023-06",
            "highlights": ["Built API test suites with TestNG.", "Automated regression runs."],
        }
    ],
    "skills": [{"name": "Testing", "keywords": ["TestNG", "Selenium"]}],
}
VERIFIED_FACTS: list[dict] = [
    {"id": "1", "kind": "job", "title": "Test Automation Engineer", "org": "Acme",
     "bullets": [{"id": "b1", "text": "Built API test suites with TestNG."}]}
]
GITHUB_CONTEXT: dict = {}


def source_numbers() -> set[str]:
    return set(
        NUMBER_RE.findall(
            _resume_text(SOURCE_DOC)
            + "\n"
            + json.dumps(VERIFIED_FACTS, ensure_ascii=False)
            + "\n"
            + json.dumps(GITHUB_CONTEXT, ensure_ascii=False)
        )
    )


def run_guard(hostile_bullet: str, *, field: str = "highlight") -> tuple[bool, str]:
    """Put hostile text through the real guard. Returns (survived, resulting_text)."""
    revised = json.loads(json.dumps(SOURCE_DOC))
    if field == "highlight":
        revised["work"][0]["highlights"] = [hostile_bullet, "Automated regression runs."]
    elif field == "summary":
        revised["basics"]["summary"] = hostile_bullet
    elif field == "skills":
        revised["skills"][0]["keywords"] = ["TestNG", "Selenium", hostile_bullet]
    elif field == "project_title":
        revised["projects"] = [{"name": hostile_bullet, "highlights": ["Did a thing."]}]

    new_numbers = set(NUMBER_RE.findall(_resume_text(revised)))
    unsupported = new_numbers - source_numbers()
    cleaned, _blocked = _strip_unverified_numbers(
        revised, original=SOURCE_DOC, unsupported=unsupported
    )
    text = _resume_text(cleaned)
    return hostile_bullet in text, text


# --- forms the regex DOES catch -------------------------------------------------

@pytest.mark.parametrize(
    "bullet",
    [
        "Reduced latency by 40%.",
        "Scaled the platform to 10,000,000 users.",
        "Cut runtime to 250ms.",
        "Saved $50,000 annually.",
        "Improved throughput 3x.",
        "Made the suite 40%-faster than before.",
        "Cut build time to 12 min.",
        # Fullwidth digits ARE caught: Python's \d is Unicode-aware and matches
        # U+FF10..U+FF19. Worth pinning, because it is the one evasion I expected
        # to work and it does not.
        "Reduced latency by ４０％.",
    ],
)
def test_digit_forms_are_stripped(bullet: str) -> None:
    survived, _ = run_guard(bullet)
    assert not survived, f"digit form survived: {bullet!r}"


# --- forms the regex does NOT catch --------------------------------------------

@pytest.mark.parametrize(
    ("label", "bullet"),
    [
        ("spelled-out percent", "Reduced latency by forty percent."),
        ("spelled-out range", "Delivered three to five years of automation coverage."),
        ("spelled-out scale", "Scaled the platform to ten million users."),
        ("spelled-out multiple", "Improved throughput threefold."),
        ("roman numeral", "Cut defect escape rate by XL percent."),
        ("unicode fraction", "Cut regression time by ½."),
        ("unicode fraction 3/4", "Reduced flake rate by ¾ across the suite."),
        ("superscript", "Handled 10² concurrent sessions."),
        ("circled digit", "Owned ⑤ release trains."),
        ("word-boundary evasion", "Achieved a99% pass rate."),
        ("hyphen-glued word", "Ran a twenty-percent faster pipeline."),
    ],
)
def test_non_digit_numeric_forms_survive(label: str, bullet: str) -> None:
    """Each of these is an unverified quantitative claim that reaches the page."""
    survived, _ = run_guard(bullet)
    assert survived, f"{label}: unexpectedly stripped (guard is better than assumed)"


@pytest.mark.parametrize("field", ["summary", "skills", "project_title"])
def test_guard_covers_non_bullet_fields(field: str) -> None:
    """The brief asks whether the stripper only walks bullets. It walks everything."""
    survived, _ = run_guard("Reduced latency by 40%.", field=field)
    assert not survived, f"digit form survived in {field}"


# --- the reverse failure: over-refusal -----------------------------------------

def test_reformatting_a_verified_number_reads_as_unsupported() -> None:
    """String comparison, not numeric: 1,000 and 1000 are different tokens.

    A verified fact saying "1,000" plus a rewrite saying "1000" means the guard
    treats the rewrite as an invented metric and reverts it.
    """
    doc = json.loads(json.dumps(SOURCE_DOC))
    doc["work"][0]["highlights"] = ["Ran 1,000 regression cases nightly."]
    facts = [{"id": "1", "bullets": [{"text": "Ran 1,000 regression cases nightly."}]}]

    src = set(
        NUMBER_RE.findall(
            _resume_text(doc) + "\n" + json.dumps(facts) + "\n" + json.dumps({})
        )
    )
    revised = json.loads(json.dumps(doc))
    revised["work"][0]["highlights"] = ["Ran 1000 regression cases nightly."]
    new = set(NUMBER_RE.findall(_resume_text(revised)))
    unsupported = new - src

    assert unsupported == {"1000"}, unsupported
    cleaned, blocked = _strip_unverified_numbers(revised, original=doc, unsupported=unsupported)
    assert blocked, "expected the reformatted-but-true number to be blocked"
    assert cleaned["work"][0]["highlights"] == ["Ran 1,000 regression cases nightly."]


def test_split_across_whitespace_changes_the_token() -> None:
    """A number broken by a space extracts as different tokens than the whole."""
    assert set(NUMBER_RE.findall("40%")) == {"40%"}
    assert set(NUMBER_RE.findall("4 0%")) == {"4", "0%"}
