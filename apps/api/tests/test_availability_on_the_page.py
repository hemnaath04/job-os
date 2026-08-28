"""A posting that asks when you can start gets an answer, or an honest gap.

An internship posting told applicants to "state your availability including
start and end dates". The page that came back mentioned no date above the
education block, so the one thing the posting told the recruiter to look for was
not findable in ten seconds.

The fix cannot be "have the model write a start date", because a start date the
model works out from a graduation year is a start date nobody verified. So the
line is assembled in Python from dates the profile holds, and a profile holding
none produces a gap card instead.

Every fixture here is a made-up candidate at a made-up school. The behaviour has
to hold for a marketing student with one degree and no repositories.
"""
from __future__ import annotations

from datetime import date

from job_os.services.availability import (
    derive_availability,
    posting_asks_for_availability,
)
from job_os.services.tailor import TailorFact

TODAY = date(2026, 8, 27)

DEGREE = TailorFact(
    id="edu",
    kind="education",
    title="BS Information Systems",
    org="Lakeside State University",
    start_date=date(2024, 9, 1),
    end_date=date(2028, 5, 1),
)


# --- does this posting actually ask? -----------------------------------------


def test_the_instruction_the_repro_posting_used_is_recognised() -> None:
    jd = (
        "About the team. You will support the platform group.\n"
        "Please state your availability including start and end dates."
    )
    assert posting_asks_for_availability({}, jd)


def test_a_posting_asking_for_an_expected_graduation_date_counts() -> None:
    assert posting_asks_for_availability(
        {"qualifications": ["Expected graduation date between 2027 and 2029"]}, ""
    )


def test_the_ask_is_found_when_it_arrives_only_in_the_parsed_fields() -> None:
    # Some ATS exports put the application instructions in responsibilities and
    # leave them out of the description body entirely.
    assert posting_asks_for_availability(
        {"responsibilities": ["Let us know when you can start"]}, ""
    )


def test_uptime_language_is_not_an_availability_question() -> None:
    """"High availability" is the single most common false positive here.

    A posting for a platform role says it constantly, and reading it as a
    question would put a graduation date on the header of every backend resume
    the product ever writes.
    """
    jd = (
        "You will keep a high availability service running and improve the "
        "availability of our internal tools."
    )
    assert not posting_asks_for_availability({}, jd)


def test_a_posting_stating_its_own_start_date_is_not_asking() -> None:
    assert not posting_asks_for_availability(
        {}, "The internship starts in June and runs for twelve weeks."
    )


# --- what the page is allowed to say -----------------------------------------


def test_a_graduation_month_is_enough_to_answer_the_recruiter() -> None:
    # The commonest profile in the product: one degree, real dates, nothing
    # recorded about availability. The graduation month is verified and it is
    # the date the recruiter is scanning for, so it earns the line.
    assert derive_availability([DEGREE], today=TODAY).line == "Graduating May 2028"


def test_a_finished_degree_is_not_described_as_still_running() -> None:
    graduated = TailorFact(
        id="edu",
        kind="education",
        title="BA Communications",
        org="Lakeside State University",
        end_date=date(2024, 6, 1),
    )
    assert derive_availability([graduated], today=TODAY).line == "Graduated June 2024"


def test_the_candidates_own_window_wins_over_the_graduation_date() -> None:
    stated = TailorFact(
        id="avail",
        kind="availability",
        title="Availability",
        start_date=date(2027, 5, 1),
        end_date=date(2027, 8, 1),
    )
    result = derive_availability([DEGREE, stated], today=TODAY)

    assert result.line == "Available May 2027 to August 2027"
    assert result.explicit, "a stated window is the thing the posting asked for"


def test_a_window_recorded_on_a_payload_is_read_too() -> None:
    # Facts arrive from resume imports as much as from the fact editor, and an
    # import puts anything it cannot map onto a column into the payload.
    carried = TailorFact(
        id="p",
        kind="experience",
        title="Operations Assistant",
        org="Lakeside Grocery",
        payload={"availability": "Available from January 2027"},
    )
    assert derive_availability([carried], today=TODAY).line == "Available from January 2027"


def test_work_authorization_is_carried_next_to_the_window() -> None:
    stated = TailorFact(
        id="avail",
        kind="availability",
        title="Availability",
        start_date=date(2027, 5, 1),
        end_date=date(2027, 8, 1),
        payload={"work_authorization": "Authorized to work in the US without sponsorship"},
    )
    line = derive_availability([stated], today=TODAY).line

    assert line == (
        "Available May 2027 to August 2027, "
        "Authorized to work in the US without sponsorship"
    )


def test_a_profile_with_no_dates_says_nothing_at_all() -> None:
    """The whole point. Silence, then a gap card, never a guessed date."""
    undated = [
        TailorFact(id="s", kind="skill", title="Customer research"),
        TailorFact(id="x", kind="experience", title="Barista", org="Lakeside Coffee"),
    ]
    assert not derive_availability(undated, today=TODAY)


def test_a_project_with_dates_is_not_mistaken_for_an_availability_window() -> None:
    # A project's own dates say when the project ran. Reading them as a hiring
    # window is exactly the invention this module exists to refuse.
    project = TailorFact(
        id="proj",
        kind="project",
        title="Neighbourhood Recipe Swap",
        start_date=date(2025, 2, 1),
        end_date=date(2025, 6, 1),
    )
    assert not derive_availability([project], today=TODAY)


def test_a_paragraph_saved_under_availability_does_not_wrap_the_header() -> None:
    rambling = TailorFact(
        id="a",
        kind="availability",
        title="Availability",
        payload={
            "availability": (
                "Available from May 2027. I can also start earlier if the team "
                "needs it, and I am happy to discuss a part time arrangement "
                "over the spring semester if that suits better."
            )
        },
    )
    assert derive_availability([rambling], today=TODAY).line == "Available from May 2027"


def test_the_latest_degree_decides_the_graduation_date() -> None:
    earlier = TailorFact(
        id="bs",
        kind="education",
        title="BS Information Systems",
        org="Lakeside State University",
        end_date=date(2026, 5, 1),
    )
    later = TailorFact(
        id="ms",
        kind="education",
        title="MS Analytics",
        org="Riverbend University",
        end_date=date(2028, 12, 1),
    )
    assert derive_availability([earlier, later], today=TODAY).line == "Graduating December 2028"
