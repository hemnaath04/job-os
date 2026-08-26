"""The finalize review ran the same checks as the tailor, and never got the vault.

#46 taught `document_quality_flags` to tell a bullet the writer padded from one
printed exactly as the candidate wrote it, and #48 taught `unevidenced_skills` to
ask whether anything backs a skill rather than whether this page had room for it.
Both take the vault as a keyword argument, and both default to the old behaviour
when it is absent.

`resume_engine` never passed it. So a real post-deploy run had two disagreeing
reports on one document: `ats_report.writing_flags` said `too_long_verbatim(46w)`
and named no unevidenced skills, while `review_report.issues`, the list the
finalize gate actually scores, said `too_long(46w)` and `unevidenced_skill(+31)`
and failed the resume for both.

There was a second bug underneath. `too_long_verbatim(46w)` starts with
`too_long`, and the severity test was a string prefix match, so even once the
vault arrived the inherited flag would have kept its warning and kept costing
points.
"""
from __future__ import annotations

from job_os.services.resume_engine import (
    deterministic_review,
    is_substantive,
    provisional_review,
    vault_text,
)

CLAIMFARM = (
    "Built an AI agent that turns a farmer's crop photo into a filed insurance "
    "claim in under a minute: a vision model grades damage, weather corroborates "
    "it, embeddings retrieve similar claims, and an LLM drafts a localized "
    "confirmation in 10 languages, behind a 6-signal fraud check."
)
VAULT = [
    {
        "kind": "project",
        "title": "ClaimFarm",
        "payload": {"keywords": ["Python", "FastAPI"]},
        "bullets": [{"text": CLAIMFARM}],
    },
    {"kind": "skill", "title": "Selenium", "org": "Testing & CI/CD", "bullets": []},
    {"kind": "skill", "title": "Jenkins", "org": "Testing & CI/CD", "bullets": []},
]
DOC = {
    "projects": [{"name": "ClaimFarm", "highlights": [CLAIMFARM]}],
    "skills": [{"name": "Testing & CI/CD", "keywords": ["Selenium", "Jenkins"]}],
}


def flags_in(issues) -> str:
    return " ".join(issue.message for issue in issues if issue.code == "bullet_writing")


def test_the_review_now_names_his_wording_as_his() -> None:
    issues, _pages, _text = deterministic_review(DOC, b"", VAULT)
    assert "too_long_verbatim(46w)" in flags_in(issues)
    assert "too_long(46w)" not in flags_in(issues)


def test_the_review_stops_failing_his_verified_skills() -> None:
    issues, _pages, _text = deterministic_review(DOC, b"", VAULT)
    assert "unevidenced_skill" not in flags_in(issues)


def test_without_a_vault_it_reviews_an_upload_exactly_as_before() -> None:
    issues, _pages, _text = deterministic_review(DOC, b"")
    assert "too_long(46w)" in flags_in(issues)
    assert "unevidenced_skill" in flags_in(issues)


def test_an_inherited_flag_is_a_note_not_a_warning() -> None:
    """The severity is what the score reads, so naming it right is not enough."""
    issues, _pages, _text = deterministic_review(DOC, b"", VAULT)
    # The bullet's own issue, not the page's missing education or links, which
    # share this code and are genuinely warnings.
    bullet = [issue for issue in issues if "ClaimFarm" in issue.message]
    assert bullet, "expected the bullet to still be reported"
    assert all(issue.severity == "suggestion" for issue in bullet)


def test_the_prefix_trap_that_would_have_survived_the_vault_fix() -> None:
    """`too_long_verbatim` starts with `too_long`. Prefix matching charged it."""
    assert is_substantive("too_long(46w)") is True
    assert is_substantive("too_long_verbatim(46w)") is False
    assert is_substantive("unevidenced_skill(Kubernetes)") is True
    assert is_substantive("repeated_opening_verb(built)") is False
    assert is_substantive("repeated_opening_verb_verbatim(built)") is False


def test_the_draft_review_inside_the_tailor_stops_charging_for_them() -> None:
    """`provisional_review` is what the tailor writes onto the version row.

    Asserted on the penalty rather than the score, because this deliberately
    minimal document has no education and no links and so floors at zero either
    way. The penalty is the number the finalize gate actually moves.
    """
    with_vault = provisional_review(DOC, VAULT)
    without = provisional_review(DOC)
    assert with_vault.score_breakdown["warning"] < without.score_breakdown["warning"]
    assert with_vault.score_breakdown["total_penalty"] < without.score_breakdown["total_penalty"]


def test_the_vault_reader_survives_what_arrives_over_http() -> None:
    """`/resumes/render-review` takes this from the browser, so it is untrusted."""
    sources, evidence = vault_text(
        [
            "not a fact",
            {"title": "Real", "payload": "not a dict", "bullets": "not a list"},
            {"title": "Kept", "bullets": [None, {"text": "A real bullet."}, {}]},
            None,
        ]
    )
    assert sources == ["A real bullet."]
    assert "Kept" in evidence and "Real" in evidence


def test_the_vault_reader_finds_titles_orgs_and_payload_keywords() -> None:
    _sources, evidence = vault_text(VAULT)
    for expected in ("ClaimFarm", "Python", "FastAPI", "Selenium", "Testing & CI/CD"):
        assert expected in evidence


def test_no_vault_at_all_is_not_a_crash() -> None:
    assert vault_text(None) == ([], [])
