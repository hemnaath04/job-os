"""Refusing the postings that already said no, and only those.

Every case here is a real posting from one week of one person's search, and the
pairs are the point. Leidos and BNY get refused; AMD and Amex, which read almost
identically, do not. A gate that cannot tell those apart is worse than no gate:
it either wastes three model calls on a rejected application, or it silently
deletes a job the candidate was eligible for and never says so.

The false-refusal tests are the ones that matter. A wrong pass costs a few
minutes and a page the user throws away. A wrong refusal costs an application
they never learn they could have made, and nothing in the interface would show
it happened.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.schemas.enrichment import Eligibility, JobEnrichment  # noqa: E402
from job_os.schemas.me import WorkEligibility  # noqa: E402
from job_os.services.eligibility_gate import evaluate  # noqa: E402

# The candidate the gate was built for: can work now, will need a petition
# later, cannot hold a clearance, not a US person for export control.
F1_STUDENT = WorkEligibility(
    status="f1_student",
    cpt_eligible_now=True,
    needs_future_sponsorship=True,
    us_person_for_export_control=False,
    clearance_eligible=False,
)

# Somebody the gate should never stop for any of these reasons.
CITIZEN = WorkEligibility(
    status="us_citizen",
    cpt_eligible_now=True,
    needs_future_sponsorship=False,
    us_person_for_export_control=True,
    clearance_eligible=True,
)


def posting(*, commitment: list[str] | None = None, **gates: object) -> JobEnrichment:
    return JobEnrichment(
        eligibility=Eligibility(**gates),  # type: ignore[arg-type]
        commitment=commitment or [],  # type: ignore[arg-type]
    )


# ── The hard walls ──


def test_a_cleared_role_is_refused() -> None:
    """Leidos. The one refusal with a statute behind it.

    EO 12968 s 3.1(b) grants clearance eligibility only to US citizens, and
    32 CFR 117.10(l) rules out even a temporary grant, so there is no route an
    applicant could take. A resume cannot answer this.
    """
    reading = evaluate(posting(security_clearance="required"), F1_STUDENT)

    assert reading.verdict == "refuse"
    assert reading.refusals[0].reason == "security_clearance_required"


def test_a_citizens_only_role_is_refused() -> None:
    reading = evaluate(posting(citizenship_required=True), F1_STUDENT)

    assert reading.verdict == "refuse"
    assert reading.refusals[0].reason == "citizenship_required"


def test_a_preferred_clearance_is_only_flagged() -> None:
    """Preferred is not required, and the difference is a whole application."""
    reading = evaluate(posting(security_clearance="preferred"), F1_STUDENT)

    assert reading.verdict == "flag"


# ── The sponsorship pair: the line the whole module exists for ──

BNY = (
    "You must be legally authorized to work in the United States without the "
    "need for employer sponsorship now or in the future."
)
AMD = (
    "AMD will not provide visa sponsorship for this internship position. "
    "Candidates must be authorized to work in the US at the time of hire."
)


def test_no_sponsorship_now_or_in_the_future_is_refused() -> None:
    """BNY and Honeywell. The candidate cannot satisfy this however they start.

    Employer policy rather than law, and waivable in principle, which makes it
    the weakest of the three refusals. It is still a refusal because the
    posting stated a requirement about the candidate, in the words the
    candidate answers on the application form, and this candidate does not meet
    it.
    """
    reading = evaluate(
        posting(visa_sponsorship="no"), F1_STUDENT, jd_text=BNY
    )

    assert reading.verdict == "refuse"
    assert reading.refusals[0].reason == "no_sponsorship_now_or_future"


def test_no_sponsorship_for_this_internship_is_only_flagged() -> None:
    """AMD and Amex, which read almost identically to BNY and are not the same.

    CPT is authorized by the school's DSO under 8 CFR 214.2(f)(10)(i), and
    8 CFR 274a.12(b)(6)(iii) states that no USCIS endorsement is necessary, so
    an employer who files nothing can still lawfully take this candidate. The
    posting declined to sponsor and said nothing about later.
    """
    reading = evaluate(
        posting(visa_sponsorship="no", commitment=["internship"]),
        F1_STUDENT,
        jd_text=AMD,
    )

    assert reading.verdict == "flag"
    assert reading.flags[0].reason == "no_sponsorship_this_role_only"
    assert "school" in reading.flags[0].detail, "the flag has to say what to check"


def test_the_two_sponsorship_clauses_are_not_confused_for_each_other() -> None:
    """Stated as one assertion, because getting it backwards is the failure.

    Both directions are expensive and the error is invisible either way.
    """
    refused = evaluate(posting(visa_sponsorship="no"), F1_STUDENT, jd_text=BNY)
    flagged = evaluate(
        posting(visa_sponsorship="no", commitment=["internship"]),
        F1_STUDENT,
        jd_text=AMD,
    )

    assert (refused.verdict, flagged.verdict) == ("refuse", "flag")


def test_a_non_training_role_that_does_not_mention_the_future_is_flagged() -> None:
    """A full-time posting saying only "no sponsorship".

    Likely about the ongoing relationship, and refusing on that guess is
    exactly the wrong-refusal this module is careful about. The posting did not
    say it, so the gate does not claim it did.
    """
    reading = evaluate(
        posting(visa_sponsorship="no", commitment=["full-time"]),
        F1_STUDENT,
        jd_text="We do not provide visa sponsorship for this role.",
    )

    assert reading.verdict == "flag"
    assert reading.flags[0].reason == "no_sponsorship_unclear_term"


# ── Export control: flagged, never refused ──


@pytest.mark.parametrize(
    "text",
    [
        "This position requires access to export-controlled technology. "
        "Applicants must be a US person as defined by ITAR.",
        "Some roles may require US person status under the EAR.",
    ],
)
def test_export_control_never_refuses(text: str) -> None:
    """The highest-risk false refusal, and the reason it is not one.

    The Justice Department's Immigrant and Employee Rights section states that
    the ITAR and the EAR "don't contain employment or hiring requirements" and
    lists "don't use the ITAR or the EAR as a reason to limit jobs to
    candidates with certain citizenships" as a best practice. Postings carry
    this text as legal boilerplate with no citizenship requirement attached, so
    refusing on it would quietly discard real jobs.

    The term is not even a clean binary: under ITAR (22 CFR 120.62) this
    candidate is a foreign person, while 15 CFR 772.1(a)(3) makes "any person
    in the United States" a US person for a specific list of EAR prohibition
    sections. A gate refusing here would be refusing on a question with two
    correct answers.
    """
    reading = evaluate(
        posting(work_authorization_required=True), F1_STUDENT, jd_text=text
    )

    assert reading.verdict == "flag"
    assert not reading.refusals


# ── Nothing is claimed on behalf of somebody who never answered ──


def test_an_account_that_never_opened_settings_is_never_refused() -> None:
    """The default has to be indistinguishable from before this existed.

    `clearance_eligible` defaults to False, and that default alone must not
    stop a run: "we never asked" and "they cannot" are different answers.
    """
    never_asked = WorkEligibility()

    for gates in (
        posting(security_clearance="required"),
        posting(citizenship_required=True),
        posting(visa_sponsorship="no"),
        posting(work_authorization_required=True),
    ):
        assert evaluate(gates, never_asked, jd_text=BNY).verdict == "pass"


def test_a_posting_with_no_eligibility_clauses_passes() -> None:
    assert evaluate(posting(), F1_STUDENT).verdict == "pass"


def test_a_candidate_who_meets_everything_passes() -> None:
    reading = evaluate(
        posting(
            security_clearance="required",
            citizenship_required=True,
            visa_sponsorship="no",
            work_authorization_required=True,
        ),
        CITIZEN,
        jd_text=BNY,
    )

    assert reading.verdict == "pass"


def test_an_unenriched_posting_is_never_refused() -> None:
    """A job whose second pass never ran says nothing about eligibility.

    Silence is not a clause. This is the common case for any job saved before
    enrichment reached the ingest path.
    """
    assert evaluate(None, F1_STUDENT).verdict == "pass"


def test_the_refusal_message_says_what_and_what_to_do() -> None:
    """A stop with no reason reads as a bug and gets retried."""
    message = evaluate(posting(citizenship_required=True), F1_STUDENT).message()

    assert "citizenship" in message.lower()
    assert "misread" in message, "the user needs a way to disagree with it"


# ── The gate as the tailor actually runs it ──


@pytest.mark.asyncio
async def test_the_tailor_stops_before_a_single_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The saving, and the reason this is a gate rather than a warning.

    A run spends three sequential model calls and several minutes producing a
    page. Refusing here costs a dictionary lookup. `run_tailor` is called with
    no fake client installed at all, so reaching the model would raise
    something other than `TailorInputError` and this would fail.
    """
    from types import SimpleNamespace

    from job_os.services import tailor
    from job_os.services.job_enrich import ENRICHMENT_KEY

    # A real-looking key and an unroutable base URL. No fake client is
    # installed, so a run that reached the model would fail on the network
    # rather than raise `TailorInputError`, which is what makes this an
    # assertion about ordering and not just about the exception type.
    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
            analyst_effort=None,
        ),
    )

    jd_parsed = {
        "technologies": ["Python"],
        ENRICHMENT_KEY: posting(security_clearance="required").model_dump(mode="json"),
    }

    with pytest.raises(tailor.TailorInputError) as raised:
        await tailor.run_tailor(
            facts=[],
            bullets_by_fact={},
            master_json_resume={"basics": {}},
            jd_parsed=jd_parsed,
            jd_clean="Cleared position. TS/SCI required.",
            candidate_eligibility=F1_STUDENT,
        )

    assert "clearance" in str(raised.value).lower()


@pytest.mark.asyncio
async def test_an_eligibility_flag_reaches_the_user_as_a_gap() -> None:
    """A flag nobody sees is the same as no flag.

    It rides the `gap_questions` channel rather than a new one: "this posting
    says it will not sponsor" is something to check before applying, which is
    what that list already means and what the tailor page already renders.
    """
    from job_os.services import eligibility_gate, tailor

    reading = eligibility_gate.evaluate(
        posting(visa_sponsorship="no", commitment=["internship"]),
        F1_STUDENT,
        jd_text=AMD,
    )
    gaps = tailor._eligibility_gaps(reading)

    assert len(gaps) == 1
    assert gaps[0].why_no_match == "no_sponsorship_this_role_only"
    assert "sponsor" in gaps[0].requirement.lower()
