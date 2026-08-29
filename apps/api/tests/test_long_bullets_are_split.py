"""An over-length bullet is cut, or it is raised with the person who wrote it.

A verified bullet saved at 36 words prints as three lines and buries whatever
the reader was meant to take from it. Both of the engine's existing rules were
right and they disagreed: the prompt says 30 words, and the safest thing a
writer can do with a verified bullet is print it exactly as saved. The page used
to resolve that by printing the long bullet and reporting `too_long_verbatim`,
which is honest and leaves the page exactly as bad as it was.

So the page cuts what it can cut without writing anything: only at punctuation
the author already used. A bullet with no such break comes back untouched and
becomes a gap card, because shortening it means deciding which of the
candidate's claims to drop and that decision is theirs.
"""
from __future__ import annotations

from job_os.schemas.resumes import SelectedBullet
from job_os.services.resume_writing import (
    BULLET_MAX_WORDS,
    over_length_bullets,
    split_long_bullet,
)
from job_os.services.tailor import _split_over_length

# 36 words, two sentences, the shape the repro bullet had.
TWO_STATEMENTS = (
    "Built the nightly regression suite for the payments service and wired it "
    "into the deployment pipeline so a failing run blocks the release. "
    "Investigated the flaky cases each morning and rewrote the fixtures that "
    "were causing them to fail."
)

# 34 words, one sentence, no break anywhere. Nothing can cut this honestly.
ONE_LONG_SENTENCE = (
    "Worked with the operations team on a weekly reporting process that pulled "
    "numbers from three separate spreadsheets and turned them into a single "
    "summary the regional managers could read without asking anyone for help "
    "first"
)


def test_a_short_bullet_is_returned_untouched() -> None:
    short = "Wrote the nightly reconciliation job."
    assert split_long_bullet(short) == [short]


def test_two_sentences_become_two_bullets() -> None:
    pieces = split_long_bullet(TWO_STATEMENTS)

    assert len(pieces) == 2
    assert all(len(piece.split()) <= BULLET_MAX_WORDS for piece in pieces)
    assert pieces[0].startswith("Built the nightly regression suite")
    assert pieces[1].startswith("Investigated the flaky cases")


def test_the_split_adds_no_words_of_its_own() -> None:
    """The whole safety argument. The output is the input, cut."""
    pieces = split_long_bullet(TWO_STATEMENTS)
    original = TWO_STATEMENTS.split()
    produced = " ".join(pieces).split()

    assert len(produced) == len(original)
    assert [word.casefold() for word in produced] == [
        word.casefold() for word in original
    ]


def test_a_semicolon_is_a_break_the_author_already_wrote() -> None:
    bullet = (
        "Migrated the build from a hand-rolled shell script to a hosted "
        "pipeline; cut the time a change waits before anyone can review it and "
        "removed the one machine every release depended on"
    )
    pieces = split_long_bullet(bullet)

    assert len(pieces) == 2
    assert not pieces[0].endswith(";"), "the clause gets a clean end, not a stray mark"
    assert pieces[1].startswith("Cut the time")


def test_a_long_sentence_with_no_break_is_left_alone() -> None:
    # Splitting this needs a verb nobody wrote. It stays whole and becomes a gap.
    assert split_long_bullet(ONE_LONG_SENTENCE) == [ONE_LONG_SENTENCE]


def test_a_break_that_would_orphan_a_fragment_is_not_used() -> None:
    bullet = (
        "Rebuilt the customer onboarding checklist so a new account reaches its "
        "first successful invoice without a support call, which took three "
        "rounds of feedback from the team. It worked."
    )
    assert split_long_bullet(bullet) == [" ".join(bullet.split())], (
        "a two-word second bullet reads worse than the long one did"
    )


def test_the_selected_bullets_keep_pointing_at_the_fact_they_came_from() -> None:
    """Both halves stay traceable to the one verified bullet behind them."""
    selected = [
        SelectedBullet(
            fact_bullet_id="bullet-1",
            rewritten_text=TWO_STATEMENTS,
            target_section="work",
        )
    ]
    split = _split_over_length(selected)

    assert len(split) == 2
    assert {piece.fact_bullet_id for piece in split} == {"bullet-1"}
    assert {piece.target_section for piece in split} == {"work"}


def test_a_bullet_that_cannot_be_split_survives_the_pass_unchanged() -> None:
    selected = [
        SelectedBullet(
            fact_bullet_id="bullet-2",
            rewritten_text=ONE_LONG_SENTENCE,
            target_section="projects",
        )
    ]
    assert _split_over_length(selected) == selected


def test_the_page_reports_what_it_could_not_shorten() -> None:
    document = {
        "work": [
            {
                "position": "Operations Analyst",
                "highlights": ["Wrote the weekly report.", ONE_LONG_SENTENCE],
            }
        ],
        "projects": [{"name": "Recipe Swap", "highlights": ["Shipped it."]}],
    }
    assert over_length_bullets(document) == [ONE_LONG_SENTENCE]


def test_a_page_within_the_cap_reports_nothing() -> None:
    document = {"work": [{"position": "Analyst", "highlights": ["Wrote the report."]}]}
    assert over_length_bullets(document) == []
