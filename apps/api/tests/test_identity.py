from __future__ import annotations

from job_os.services.identity import fact_identity, identity_text


def test_punctuation_and_accents_do_not_change_identity() -> None:
    assert identity_text("Northeastern University - Khoury College") == identity_text(
        "Northeastern University, Khoury College"
    )
    assert identity_text("Crème Brûlée Co.") == identity_text("Creme Brulee Co")
    assert identity_text(None) == ""


def test_the_real_duplicate_epam_facts_now_collapse() -> None:
    """The exact pair that put EPAM Systems on the resume twice.

    Two imports of the same resume worded the title differently, and the old
    exact-title key treated the second as a new job.
    """
    long_title = {
        "kind": "experience",
        "org": "EPAM Systems",
        "title": (
            "Junior Software Test Automation Engineer, Client: leading global "
            "rideshare platform (Fares team)"
        ),
        "start_date": "2024-07-01",
        "end_date": "2025-12-01",
    }
    short_title = {
        "kind": "experience",
        "org": "EPAM Systems",
        "title": "Software Test Automation Engineer",
        "start_date": "2024-07-01",
        "end_date": "2025-12-01",
    }
    assert fact_identity(long_title) == fact_identity(short_title)


def test_the_real_duplicate_northeastern_facts_now_collapse() -> None:
    """Same degree, institution punctuated two different ways."""
    dashed = {
        "kind": "education",
        "org": "Northeastern University — Khoury College of Computer Sciences",
        "title": "Master of Science Computer Science",
        "start_date": "2026-01-01",
        "end_date": "2028-05-01",
    }
    comma = {
        "kind": "education",
        "org": "Northeastern University, Khoury College of Computer Sciences",
        "title": "Master of Science Computer Science",
        "start_date": "2026-01-01",
        "end_date": "2028-05-01",
    }
    assert fact_identity(dashed) == fact_identity(comma)


def test_two_real_stints_at_one_employer_stay_distinct() -> None:
    """Deduping must not swallow a promotion or a return to the same company."""
    first = {
        "kind": "experience",
        "org": "EPAM Systems",
        "title": "Engineer",
        "start_date": "2022-01-01",
        "end_date": "2023-01-01",
    }
    second = {
        "kind": "experience",
        "org": "EPAM Systems",
        "title": "Senior Engineer",
        "start_date": "2024-07-01",
        "end_date": "2025-12-01",
    }
    assert fact_identity(first) != fact_identity(second)


def test_two_degrees_at_one_school_stay_distinct() -> None:
    bachelors = {
        "kind": "education",
        "org": "Example University",
        "title": "Bachelor of Science",
        "start_date": "2018-09-01",
        "end_date": "2022-05-01",
    }
    masters = {
        "kind": "education",
        "org": "Example University",
        "title": "Master of Science",
        "start_date": "2022-09-01",
        "end_date": "2024-05-01",
    }
    assert fact_identity(bachelors) != fact_identity(masters)


def test_skills_are_keyed_on_title_since_they_have_no_dates() -> None:
    python = {"kind": "skill", "org": "Languages", "title": "Python"}
    same = {"kind": "skill", "org": "languages", "title": " python "}
    other = {"kind": "skill", "org": "Languages", "title": "Go"}
    assert fact_identity(python) == fact_identity(same)
    assert fact_identity(python) != fact_identity(other)


def test_a_certificate_reworded_slightly_is_still_the_same_certificate() -> None:
    a = {"kind": "certification", "org": "Amazon Web Services",
         "title": "AWS Certified Cloud Practitioner"}
    b = {"kind": "certification", "org": "Amazon Web Services!",
         "title": "AWS  Certified   Cloud Practitioner"}
    assert fact_identity(a) == fact_identity(b)


def test_different_kinds_never_collide() -> None:
    skill = {"kind": "skill", "org": "Acme", "title": "Testing"}
    project = {"kind": "project", "org": "Acme", "title": "Testing"}
    assert fact_identity(skill) != fact_identity(project)
