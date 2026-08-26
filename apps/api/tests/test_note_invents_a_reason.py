"""A project left off the page got an invented reason for it.

A real run kept BedRocked, ClaimFarm and Infant Cry, left job.os off, and said:
"ClaimFarm and job.os excluded, no verified fact bullets provided for them."

job.os is verified and has three bullets. The rule that sentence describes does
not exist either: `metric_verified` is never read in tailor.py and no code path
drops a fact for unverified bullets. It is an invented justification for a real
decision, which is the pattern #39 and #40 were built to end, surviving in the
one place neither looked: both corrected the SELECTION, and neither read the
prose explaining it.

#40's append is right for a note that has gone STALE. This is different. The
claim was never true, and a lie followed by a correction still reads as a lie
first, because prose is read top to bottom. So it does not get printed.
"""
from __future__ import annotations

from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _false_bullet_excuses,
    _honest_exclusion_note,
    _ProjectScore,
)

JOBOS = TailorFact(id="j", kind="project", title="job.os — AI Job-Search Platform")
CLAIMFARM = TailorFact(id="c", kind="project", title="ClaimFarm: Agentic Crop-Insurance AI")
BEDROCKED = TailorFact(id="b", kind="project", title="BedRocked — Civic Sewer Platform")
FACTS = [JOBOS, CLAIMFARM, BEDROCKED]
BULLETS = {
    "j": [TailorBullet(id="j1", fact_id="j", text="Built job.os.")],
    "c": [TailorBullet(id="c1", fact_id="c", text="Built ClaimFarm.")],
    "b": [TailorBullet(id="b1", fact_id="b", text="Built BedRocked.")],
}
SCORES = [
    _ProjectScore(fact_id="j", title=JOBOS.title, score=4, matched=()),
    _ProjectScore(fact_id="c", title=CLAIMFARM.title, score=9, matched=()),
]

REAL_NOTE = (
    "Positioned for AI engineering via BedRocked and Infant Cry. ClaimFarm and "
    "job.os excluded, no verified fact bullets provided for them."
)


def test_the_real_run_that_found_this() -> None:
    kept, accused = _false_bullet_excuses(REAL_NOTE, facts=FACTS, bullets_by_fact=BULLETS)
    assert kept == "Positioned for AI engineering via BedRocked and Infant Cry."
    assert accused == ["ClaimFarm", "job.os"]


def test_a_project_named_with_a_dot_still_ends_its_sentence_correctly() -> None:
    """"job.os" contains a period, so a naive sentence split would cut it in half."""
    note = "Led with job.os. It was excluded for having no verified bullets."
    kept, accused = _false_bullet_excuses(note, facts=FACTS, bullets_by_fact=BULLETS)
    assert kept == "Led with job.os."
    # Dropped without naming anyone: the sentence says "It", and when every
    # project has bullets the claim is false whoever it meant. Which project the
    # pronoun referred to is a guess, and guessing is the prose surgery this
    # deliberately does not attempt, so the false sentence goes and no
    # replacement reason is invented in its place.
    assert accused == []


def test_an_unattributed_bullets_excuse_survives_when_one_project_has_none() -> None:
    """Then it might be true, and dropping it would be the same sin in reverse."""
    empty = {"j": [], "c": BULLETS["c"], "b": BULLETS["b"]}
    note = "Led with ClaimFarm. It was excluded for having no verified bullets."
    kept, accused = _false_bullet_excuses(note, facts=FACTS, bullets_by_fact=empty)
    assert kept == note
    assert accused == []


def test_a_true_statement_about_a_fact_with_no_bullets_is_left_alone() -> None:
    """The reason is only false when the bullets are actually there."""
    empty = {"j": [], "c": BULLETS["c"], "b": BULLETS["b"]}
    kept, accused = _false_bullet_excuses(
        "job.os excluded, no verified fact bullets provided.",
        facts=FACTS,
        bullets_by_fact=empty,
    )
    assert accused == []
    assert "job.os excluded" in kept


def test_an_exclusion_for_some_other_reason_is_not_touched() -> None:
    """Only the bullets claim is checkable here, so only it is policed."""
    note = "Left job.os out because the posting is not about job search."
    kept, accused = _false_bullet_excuses(note, facts=FACTS, bullets_by_fact=BULLETS)
    assert kept == note
    assert accused == []


def test_praise_that_happens_to_mention_bullets_survives() -> None:
    note = "Led with job.os, whose bullets carry the strongest AI evidence."
    kept, _accused = _false_bullet_excuses(note, facts=FACTS, bullets_by_fact=BULLETS)
    assert kept == note


def test_only_the_offending_sentence_goes() -> None:
    note = (
        "Tailored for an AI engineering role. job.os excluded, no bullets provided. "
        "BedRocked leads on classification work."
    )
    kept, _accused = _false_bullet_excuses(note, facts=FACTS, bullets_by_fact=BULLETS)
    assert kept == (
        "Tailored for an AI engineering role. BedRocked leads on classification work."
    )


def test_the_replacement_names_the_real_reason_and_the_score() -> None:
    note = _honest_exclusion_note(
        ["ClaimFarm", "job.os"], on_page={"ClaimFarm", "BedRocked"}, scored=SCORES
    )
    assert "job.os (matched 4 of this posting's requirements)" in note
    assert "ranked below the projects that are" in note
    assert "it has verified bullets" in note


def test_a_project_that_is_actually_on_the_page_is_not_reported_absent() -> None:
    """ClaimFarm was named as excluded and shipped anyway. That is #40's job."""
    note = _honest_exclusion_note(["ClaimFarm"], on_page={"ClaimFarm"}, scored=SCORES)
    assert note == ""


def test_nothing_invented_means_nothing_added() -> None:
    assert _honest_exclusion_note([], on_page=set(), scored=SCORES) == ""


def test_an_empty_note_is_not_a_crash() -> None:
    assert _false_bullet_excuses("", facts=FACTS, bullets_by_fact=BULLETS) == ("", [])


# ---------------------------------------------------------------------------
# The check ate a true sentence in production. Caught in a real Jane Street run
# whose JD parsed to zero requirements:
#
#   "JD had no concrete requirements, so kept EPAM's strongest bullets plus the
#    two projects with verified evidence in this profile."
#
# True, useful, and deleted. It contains "no" and it contains "bullets", and the
# first version of this check asked for nothing more than that. Deleting a
# candidate's honest explanation is the same class of harm as printing a
# dishonest one, and this one was self-inflicted.
# ---------------------------------------------------------------------------

NO_REQUIREMENTS_NOTE = (
    "JD had no concrete requirements, so kept EPAM's strongest bullets plus "
    "the two projects with verified evidence in this profile."
)


def test_the_true_sentence_the_first_version_deleted() -> None:
    kept, accused = _false_bullet_excuses(
        NO_REQUIREMENTS_NOTE, facts=FACTS, bullets_by_fact=BULLETS
    )
    assert kept == NO_REQUIREMENTS_NOTE
    assert accused == []


def test_the_negation_has_to_attach_to_the_bullets() -> None:
    """"no requirements ... used the bullets" is not "no bullets"."""
    fine = "Found no matching role keywords, so I kept the strongest bullets."
    kept, _accused = _false_bullet_excuses(fine, facts=FACTS, bullets_by_fact=BULLETS)
    assert kept == fine


def test_the_wording_variants_that_do_mean_it_still_fire() -> None:
    for note in (
        "job.os was dropped because it lacks bullets.",
        "job.os excluded: missing verified bullets.",
        "job.os left out, without any bullets to cite.",
    ):
        kept, _accused = _false_bullet_excuses(
            note, facts=FACTS, bullets_by_fact=BULLETS
        )
        assert kept != note, f"should have been dropped: {note}"
