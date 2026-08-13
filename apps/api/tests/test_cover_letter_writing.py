"""How the letter reads, and the caps that keep it a letter.

The competitor's generated letters got called "very cheap looking", and the
reasons are a short list of habits: enthusiasm in place of information, banned
vocabulary, and a page of it. These are the rules that make those unprintable
rather than discouraged.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _cover_letter_fixtures import EPAM_TESTS, MASTER_RESUME, letter, say, vault
from job_os.schemas.cover_letters import (  # noqa: E402
    CoverLetterDocument,
    CoverLetterProvenanceEntry,
)
from job_os.services.cover_letter import (  # noqa: E402
    HARD_MAX_WORDS,
    MIN_WORDS,
    assemble_letter,
    revalidate_edited_letter,
)


def _assemble(agent):
    facts, bullets = vault()
    return assemble_letter(
        agent,
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume=MASTER_RESUME,
        company="Corvus Systems",
        role="Backend Engineer, Platform",
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "I am thrilled to apply for this role.",
        "I leveraged Python to write those suites.",
        "I would bring a robust approach to the team.",
        "I am a detail-oriented engineer with a proven track record.",
        "I utilized every tool the job asked for.",
    ],
)
def test_a_banned_word_deletes_the_whole_sentence(sentence: str) -> None:
    """Deleted rather than rewritten, and the prompt says so.

    A word swap would leave the sentence's shape, and the shape is the problem:
    "I am thrilled" is a sentence that carries no information whichever adjective
    it uses. Costing the model the whole sentence is what makes the rule bite.
    """
    result = _assemble(letter(body=[[say(sentence)]]))
    assert len(result.refused) == 1
    assert result.refused[0].reason.startswith("banned_wording(")
    assert sentence not in " ".join(result.document.paragraphs)


def test_the_ban_survives_inflection() -> None:
    """A fixed word list would miss "utilizing" and "excitement"."""
    result = _assemble(
        letter(
            body=[
                [say("I am excited about the platform work.")],
                [say("Utilizing Go was the obvious call.")],
            ]
        )
    )
    assert len(result.refused) == 2
    assert result.document.paragraphs == [
        "I am applying for the Backend Engineer, Platform role.",
        "I would welcome a conversation about the role.",
    ]


def test_an_em_dash_is_fixed_rather_than_refused() -> None:
    """The dash rule is global, and it is punctuation rather than a claim.

    Deleting a sentence over a character would throw away evidence to enforce a
    style rule, so this one is normalised on the way in. What matters is that no
    dash reaches the page, whatever the model wrote.
    """
    result = _assemble(
        letter(
            body=[
                [
                    say(
                        "I wrote Python and Go test suites — and triaged the "
                        "failures they produced.",
                        EPAM_TESTS,
                    )
                ]
            ]
        )
    )
    printed = " ".join(result.document.paragraphs)
    assert result.refused == []
    assert "—" not in printed
    assert "–" not in printed
    assert "--" not in printed
    assert "test suites, and triaged" in printed
    # The provenance row carries the printed text, not the model's original, or a
    # reader checking a claim against its bullet would be reading a third thing.
    assert "—" not in result.provenance[0].text


def test_an_exclamation_mark_never_reaches_the_page() -> None:
    result = _assemble(letter(closing=[say("I would love to talk!")]))
    assert "!" not in " ".join(result.document.paragraphs)


@pytest.mark.parametrize("pronoun", ["We built the pipeline.", "Our tests caught it."])
def test_first_person_plural_is_refused(pronoun: str) -> None:
    """In a letter about one person's work, "we" is either theft or a guess."""
    result = _assemble(letter(body=[[say(pronoun, EPAM_TESTS)]]))
    assert len(result.refused) == 1
    assert result.refused[0].reason.startswith("first_person_plural(")


def test_a_letter_past_the_hard_cap_loses_its_last_body_paragraph() -> None:
    """The cap is guaranteed by Python, not requested in the prompt.

    A cover letter that runs onto a second page is not a longer letter, it is one
    nobody finishes. Trimming takes the last body paragraph because the prompt
    asks for the strongest evidence first, and it never takes the opening or the
    closing: a letter that stops mid-argument is worse than a short one.
    """
    # Ten words a repetition, so the arithmetic is checkable: 9 + 200 + 180 + 9 + 8
    # is 406, six words past the cap, and dropping the 9-word paragraph clears it.
    filler = "I wrote and maintained the suites that covered that service. "
    result = _assemble(
        letter(
            opening=[say("I am applying for the Backend Engineer, Platform role.")],
            body=[
                [say(filler * 20, EPAM_TESTS)],
                [say(filler * 18, EPAM_TESTS)],
                [say("This last paragraph is the one that should go.", EPAM_TESTS)],
            ],
            closing=[say("I would welcome a conversation about the role.")],
        )
    )
    assert result.document.word_count == 397
    assert result.document.word_count <= HARD_MAX_WORDS
    assert "This last paragraph" not in " ".join(result.document.paragraphs)
    assert result.document.paragraphs[0].startswith("I am applying")
    assert result.document.paragraphs[-1].startswith("I would welcome")
    assert result.quality_flags["length"][-1] == "trimmed_paragraphs(1)"
    # Provenance is re-indexed against the trimmed letter, so no row can point at
    # a paragraph that is no longer there.
    for row in result.provenance:
        assert row.text in result.document.paragraphs[row.paragraph]


def test_a_thin_letter_is_flagged_rather_than_padded() -> None:
    """Nothing here can pad: every claim needs a bullet, so short means short.

    The flag is what the repair pass acts on, and if a repair cannot find more
    verified evidence then a short honest letter is the right answer.
    """
    result = _assemble(letter(body=[[say("I wrote those suites.", EPAM_TESTS)]]))
    assert result.document.word_count < MIN_WORDS
    assert result.quality_flags["length"] == [
        f"thin_letter({result.document.word_count}w)"
    ]
    assert result.quality_flags["evidence"] == ["too_few_claims(1)"]


def test_the_greeting_never_invents_a_name() -> None:
    """A hiring manager's name is the same class of fact as a phone number."""
    facts, bullets = vault()
    anonymous = assemble_letter(
        letter(),
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume=MASTER_RESUME,
        company="Corvus Systems",
        role="Backend Engineer, Platform",
    )
    named = assemble_letter(
        letter(),
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume=MASTER_RESUME,
        company="Corvus Systems",
        role="Backend Engineer, Platform",
        recipient_name="Dana Okafor",
    )
    assert anonymous.document.greeting == "Dear Hiring Team,"
    assert named.document.greeting == "Dear Dana Okafor,"


def test_the_contact_block_comes_only_from_the_resume() -> None:
    """The model is given no way to write a phone number, so it cannot."""
    result = _assemble(letter())
    sender = result.document.sender
    assert sender.name == "Hemnaath Balasubramani"
    assert sender.email == "balasubramani.h@northeastern.edu"
    assert sender.location == "Boston, MA"
    assert sender.links == ["github.com/hemnaath04", "linkedin.com/in/hemnaath"]


def test_editing_a_sentence_drops_its_provenance_row() -> None:
    """A hand edit may not inherit a claim's proof.

    The user is free to rewrite their own letter. What the system must not do is
    keep asserting that a bullet backs a sentence after the sentence changed,
    because that is provenance that no longer proves anything.
    """
    generated = _assemble(
        letter(
            body=[
                [
                    say("I wrote Python and Go test suites.", EPAM_TESTS),
                    say("I triaged the failures they produced.", EPAM_TESTS),
                ]
            ]
        )
    )
    assert len(generated.provenance) == 2

    facts, bullets = vault()
    edited = revalidate_edited_letter(
        CoverLetterDocument.model_validate(generated.document.model_dump()),
        paragraphs=[
            generated.document.paragraphs[0],
            "I wrote Python and Go test suites. I owned the entire release process.",
            generated.document.paragraphs[-1],
        ],
        provenance=[
            CoverLetterProvenanceEntry.model_validate(row.model_dump())
            for row in generated.provenance
        ],
        facts=facts,
        bullets_by_fact=bullets,
    )
    kept = {row.text for row in edited.provenance}
    assert "I wrote Python and Go test suites." in kept
    assert "I owned the entire release process." not in kept
    # The new sentence still prints. It is the user's letter; it just carries no
    # claim of proof.
    assert "I owned the entire release process." in edited.document.paragraphs[1]
