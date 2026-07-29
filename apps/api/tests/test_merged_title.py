"""Which wording of a job title reaches the page.

The real case: the profile holds two verified facts for the same EPAM role. One,
imported 2026-06-20, is titled "Junior Software Test Automation Engineer · Client:
leading global rideshare platform (Fares team)". The other, imported 2026-07-26
after the user reworded it, is titled "Software Test Automation Engineer". Ranking
by evidence printed the older wording.
"""
from __future__ import annotations

from datetime import date

from job_os.services.tailor import TailorBullet, TailorFact, _merge_duplicate_facts, _merged_title

EPAM_START = date(2024, 7, 1)
EPAM_END = date(2025, 12, 1)


def _epam(fact_id: str, title: str, updated: str) -> TailorFact:
    return TailorFact(
        id=fact_id,
        kind="experience",
        title=title,
        org="EPAM Systems",
        start_date=EPAM_START,
        end_date=EPAM_END,
        updated_at=updated,
    )


OLD = _epam(
    "old",
    "Junior Software Test Automation Engineer · Client: leading global rideshare "
    "platform (Fares team)",
    "2026-06-20T18:08:48",
)
NEW = _epam("new", "Software Test Automation Engineer", "2026-07-26T04:09:06")


def test_the_wording_saved_most_recently_wins_and_keeps_dropped_detail() -> None:
    assert _merged_title([OLD, NEW]) == (
        "Software Test Automation Engineer, Client: leading global rideshare "
        "platform (Fares team)"
    )
    # Order of the variants must not change the answer.
    assert _merged_title([NEW, OLD]) == _merged_title([OLD, NEW])


def test_the_newer_wording_wins_even_when_the_older_fact_holds_the_evidence() -> None:
    """The older fact carries five bullets and still does not win the title."""
    bullets = {
        "old": [
            TailorBullet(id=f"b{n}", fact_id="old", text=f"Did distinct thing {n}.")
            for n in range(5)
        ],
        "new": [TailorBullet(id="b9", fact_id="new", text="Did one other thing.")],
    }
    merged, _bullets = _merge_duplicate_facts([OLD, NEW], bullets)
    assert len(merged) == 1
    assert merged[0].title.startswith("Software Test Automation Engineer")
    assert "Junior" not in merged[0].title
    # The richer fact still supplies the surviving id and its evidence.
    assert merged[0].id == "old"


def test_a_qualifier_already_in_the_newer_title_is_not_repeated() -> None:
    verbose = _epam("a", "Engineer, Fares team", "2026-01-01")
    newer = _epam("b", "Engineer, Fares team", "2026-06-01")
    assert _merged_title([verbose, newer]) == "Engineer, Fares team"


def test_an_unrelated_older_title_contributes_no_qualifier() -> None:
    """Only a qualifier hanging off the same role name is carried across."""
    other = _epam("a", "Data Analyst, Reporting team", "2026-01-01")
    newer = _epam("b", "Software Engineer", "2026-06-01")
    assert _merged_title([other, newer]) == "Software Engineer"


def test_missing_timestamps_do_not_crash_the_merge() -> None:
    a = TailorFact(id="a", kind="experience", title="Engineer", org="Acme")
    b = TailorFact(id="b", kind="experience", title="Senior Engineer", org="Acme")
    assert _merged_title([a, b]) in {"Engineer", "Senior Engineer"}
