"""What an answer scaffold is allowed to say.

Every case here is a way a prep tool tells a candidate something about
themselves that is not true. The named one, the reason the guards exist at all:
a competitor's agent wrote "recognized at a national conference" into a real
user's material. The user had received no such recognition, and they would have
found that out in the room.

The rule the tests hold to is narrow and absolute. A scaffold survives only when
verified evidence carries every claim in it. Anything else is a gap, and a gap is
a useful thing to be told.
"""
from __future__ import annotations

from _interview_fixtures import vault
from job_os.schemas.interviews import (
    AnswerScaffold,
    GeneratedQuestion,
    VaultBullet,
    VaultFact,
)
from job_os.services.interview_prep import (
    _evidence_index,
    _ground_answer,
    _vault_from_supplied,
)


def _index():
    facts, bullets = vault()
    return _evidence_index(facts, bullets)


def _question(**kwargs) -> GeneratedQuestion:
    payload = {
        "question": "Tell me about a time you owned an ambiguous piece of work.",
        "topic": "ownership",
        "why_asked": "The posting asks for someone comfortable owning ambiguous work.",
    }
    payload.update(kwargs)
    return GeneratedQuestion(**payload)


def _ground(question: GeneratedQuestion, *, scaffoldable: bool = True):
    return _ground_answer(question, _index(), scaffoldable=scaffoldable)


def test_a_scaffold_that_survives_always_carries_its_provenance() -> None:
    """The contract in one assertion: no citation, no scaffold."""
    scaffold, evidence, gap, _note, _removed = _ground(
        _question(
            fact_bullet_ids=["b-js-api"],
            scaffold=AnswerScaffold(
                situation="I wanted a job search that ranked postings for me.",
                task="I had to score a posting against my own resume.",
                action="I built a FastAPI service over PostgreSQL that does the scoring.",
                result="It serves the ranked list.",
            ),
        )
    )
    assert scaffold is not None
    assert not gap
    assert evidence
    assert evidence[0].fact_bullet_id == "b-js-api"
    assert evidence[0].fact_id == "fact-jobsearcher"
    # The citation's text is OURS, copied from the verified row, not the model's
    # summary of it. A model-written citation is a claim about the evidence rather
    # than the evidence.
    assert evidence[0].text.startswith("Built a FastAPI service")


def test_an_id_the_vault_does_not_contain_is_refused_rather_than_trusted() -> None:
    """An unverified or deleted fact cannot be cited into existence.

    This is the shape of the refusal that matters: the vault handed to the
    generator holds verified rows only, so a citation outside it is a citation to
    something the user never confirmed. The tailor takes the same line with an
    analyst that invents a bullet id.
    """
    scaffold, evidence, gap, note, _removed = _ground(
        _question(
            fact_bullet_ids=["b-not-a-real-bullet"],
            scaffold=AnswerScaffold(
                situation="I led a migration of the whole platform.",
                action="I rewrote the ingest layer alone.",
            ),
        )
    )
    assert scaffold is None
    assert evidence == []
    assert gap
    assert note is not None
    assert "Nothing in your verified profile answers this yet" in note


def test_an_invented_accolade_never_reaches_the_candidate() -> None:
    """The exact failure this module was written against.

    "Recognized at a national conference" invents no number and no technology, so
    a number guard and a technology guard both pass it. It is still the tool
    telling somebody they won something.
    """
    scaffold, evidence, gap, _note, removed = _ground(
        _question(
            fact_bullet_ids=["b-br-score"],
            scaffold=AnswerScaffold(
                situation="I scored 2,404 sewer segments for a civic hackathon.",
                action="I built a six-factor 0-100 model over the segments.",
                result="The work was recognized at a national conference.",
            ),
        )
    )
    assert scaffold is not None  # the honest two thirds of the answer survive
    assert evidence
    assert not gap
    assert "recognized" not in scaffold.joined().casefold()
    assert "national conference" not in scaffold.joined().casefold()
    assert removed
    assert any("recognized" in item for item in removed)
    # The user is told what was dropped, so a real accolade can be added as
    # evidence rather than silently lost.
    assert any("claim the evidence does not record" in item for item in removed)


def test_an_accolade_the_evidence_records_survives() -> None:
    """The guard is about proof, not about vocabulary.

    A candidate who really did publish must be able to say so, or the guard
    becomes a reason to distrust the whole pack.
    """
    facts, bullets = vault()
    bullets["fact-bedrocked"].append(
        type(bullets["fact-bedrocked"][0])(
            id="b-br-award",
            fact_id="fact-bedrocked",
            text="Won the civic data track at the hackathon with the scoring model.",
        )
    )
    scaffold, _evidence, gap, _note, removed = _ground_answer(
        _question(
            fact_bullet_ids=["b-br-award"],
            scaffold=AnswerScaffold(
                situation="We entered the civic data track.",
                result="We won the track with the scoring model.",
            ),
        ),
        _evidence_index(facts, bullets),
        scaffoldable=True,
    )
    assert scaffold is not None
    assert not gap
    assert removed == []
    assert "won" in scaffold.joined().casefold()


def test_a_title_the_evidence_does_not_give_is_stripped() -> None:
    """Found by these tests, not by a user, which is the point of writing them.

    "I was given the team lead role" invents no number, names no award and
    upgrades no status, so every other guard passed it. It is also the most
    checkable claim on the list: the interviewer can ask who else was on that
    team. The verified bullet says "worked on".
    """
    scaffold, _evidence, _gap, _note, removed = _ground(
        _question(
            fact_bullet_ids=["b-epam-go"],
            scaffold=AnswerScaffold(
                situation="The pricing engine suite was flaky.",
                action="I investigated failures and fixed the flaky tests.",
                result="I was given the team lead role for it.",
            ),
        )
    )
    assert scaffold is not None
    assert "team lead" not in scaffold.joined()
    assert any("ownership or title claim" in item for item in removed)
    # The honest work survives. A guard that threw the whole answer away for one
    # bad clause would be a reason to stop reading the scaffolds.
    assert "flaky" in scaffold.joined()


def test_an_ownership_verb_the_evidence_uses_is_kept() -> None:
    """"Worked on" is what the evidence says, so "worked on" is allowed."""
    scaffold, _evidence, _gap, _note, removed = _ground(
        _question(
            fact_bullet_ids=["b-epam-go"],
            scaffold=AnswerScaffold(
                action="I worked on the Go and Python suite for the pricing engine.",
            ),
        )
    )
    assert scaffold is not None
    assert removed == []
    assert "worked on" in scaffold.joined()


def test_a_number_the_evidence_does_not_carry_is_stripped() -> None:
    scaffold, _evidence, _gap, _note, removed = _ground(
        _question(
            fact_bullet_ids=["b-epam-go"],
            scaffold=AnswerScaffold(
                situation="The pricing engine suite was flaky.",
                action="I fixed the flaky tests.",
                result="Suite runtime dropped by 40% and failures fell to 3 a week.",
            ),
        )
    )
    assert scaffold is not None
    assert "40%" not in scaffold.joined()
    assert any("number not in the evidence" in item for item in removed)
    # The sentences the evidence does carry are untouched.
    assert "flaky" in scaffold.joined().casefold()


def test_a_number_the_evidence_carries_is_kept() -> None:
    scaffold, _evidence, _gap, _note, removed = _ground(
        _question(
            fact_bullet_ids=["b-br-score"],
            scaffold=AnswerScaffold(
                action="I scored 2,404 segments with a six-factor 0-100 model.",
            ),
        )
    )
    assert scaffold is not None
    assert "2,404" in scaffold.joined()
    assert removed == []


def test_a_scaffold_cannot_promote_work_the_evidence_calls_unfinished() -> None:
    """The EPAM agent was demoed and pending approval. It did not ship.

    An answer that says it shipped is the claim an interviewer punctures with one
    follow-up, and the candidate cannot walk it back once said.
    """
    scaffold, _evidence, _gap, _note, removed = _ground(
        _question(
            fact_bullet_ids=["b-epam-agent"],
            scaffold=AnswerScaffold(
                situation="Our test cases were written by hand from requirements docs.",
                action="I worked with the team on an agent that drafts them.",
                result="We shipped it to the whole QA org.",
            ),
        )
    )
    assert scaffold is not None
    assert "shipped" not in scaffold.joined().casefold()
    assert any("says the work finished" in item for item in removed)


def test_provisional_evidence_puts_the_qualifier_in_the_answer() -> None:
    """Not a removal, a coaching line: carry the qualifier rather than dodge it."""
    scaffold, _evidence, gap, _note, _removed = _ground(
        _question(
            fact_bullet_ids=["b-epam-agent"],
            scaffold=AnswerScaffold(
                action="I worked with the team on an agent that drafts test cases.",
                result="It was demoed end to end.",
            ),
        )
    )
    assert scaffold is not None
    assert not gap
    assert "Keep the qualifier" in scaffold.result


def test_a_scaffold_whose_every_claim_fails_becomes_a_declared_gap() -> None:
    """The most important gap in the pack: the model tried and could not be honest."""
    scaffold, evidence, gap, note, removed = _ground(
        _question(
            fact_bullet_ids=["b-epam-cicd"],
            scaffold=AnswerScaffold(
                situation="I was awarded the internal engineering prize for it.",
                result="It was featured in the company newsletter.",
            ),
        )
    )
    assert scaffold is None
    assert gap
    assert note is not None
    assert "removed" in note
    assert len(removed) == 2
    # The citations survive even though the scaffold did not, so the user can see
    # which of their own work was nearest.
    assert evidence and evidence[0].fact_bullet_id == "b-epam-cicd"


def test_a_model_that_declines_to_answer_is_reported_as_a_gap_not_as_an_answer() -> None:
    scaffold, evidence, gap, note, _removed = _ground(
        _question(fact_bullet_ids=["b-epam-go"], scaffold=None)
    )
    assert scaffold is None
    assert gap
    assert evidence
    assert note is not None
    assert "does not add up to a full answer" in note


def test_first_person_is_left_alone_in_an_interview_answer() -> None:
    """A resume bullet may not say "I". A spoken answer must.

    Reusing the resume's first-person guard here would have mangled every
    scaffold in the pack.
    """
    scaffold, _evidence, _gap, _note, _removed = _ground(
        _question(
            fact_bullet_ids=["b-js-concurrency"],
            scaffold=AnswerScaffold(
                action="I wrote the scraper as a bounded worker pool.",
            ),
        )
    )
    assert scaffold is not None
    assert scaffold.action.startswith("I wrote")


def test_em_dashes_never_survive_a_scaffold() -> None:
    """The user's global rule, enforced where generated text enters the system."""
    scaffold, _evidence, _gap, _note, _removed = _ground(
        _question(
            fact_bullet_ids=["b-js-api"],
            scaffold=AnswerScaffold(
                action="I built a FastAPI service — over PostgreSQL.",
            ),
        )
    )
    assert scaffold is not None
    assert "—" not in scaffold.joined()


def test_a_browser_supplied_vault_drops_the_unverified_rows() -> None:
    """The other deployment, and the same contract.

    On the Appwrite workspace the user's facts do not live in this database, so
    the browser sends them. The client is trusted to know WHERE the facts are and
    not to decide which of them may become an answer: an unverified fact is a
    draft the user never confirmed, and a client bug must not be able to promote
    one into a scaffold.
    """
    facts, bullets_by_fact, unverified_metrics = _vault_from_supplied(
        [
            VaultFact(
                id="f1",
                kind="project",
                title="Confirmed project",
                verified=True,
                bullets=[VaultBullet(id="b1", text="Built the thing.")],
            ),
            VaultFact(
                id="f2",
                kind="experience",
                title="Draft the user never confirmed",
                verified=False,
                bullets=[VaultBullet(id="b2", text="Led the platform team.")],
            ),
            VaultFact(
                id="f3",
                kind="project",
                title="Confirmed, with an unconfirmed number",
                verified=True,
                bullets=[
                    VaultBullet(id="b3", text="Cut runtime by 40%.", metric_verified=False)
                ],
            ),
        ]
    )
    assert [fact.id for fact in facts] == ["f1", "f3"]
    assert "f2" not in bullets_by_fact
    assert unverified_metrics == {"b3"}
    # And the refusal follows through: nothing from the unverified fact can be
    # cited, because it is not in the index at all.
    assert "b2" not in _evidence_index(facts, bullets_by_fact)


def test_a_question_the_candidate_asks_gets_no_scaffold_and_is_not_a_gap() -> None:
    """There is no answer of theirs to ground, so an empty answer is correct."""
    scaffold, evidence, gap, note, removed = _ground(
        _question(question="How does the platform team split work with the data scientists?"),
        scaffoldable=False,
    )
    assert scaffold is None
    assert evidence == []
    assert not gap
    assert note is None
    assert removed == []
