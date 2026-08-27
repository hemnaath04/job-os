"""The page-fit cut traded one page defect for another.

A real run cut a three-bullet project off a nine-bullet page and shipped six:
`over_page` became `thin_page(6 bullets)`, and the project it spent to get there
was the strongest one on the page.

The cut guarded the project COUNT and never the bullet count. `MIN_PAGE_BULLETS`
existed, `thin_page` was flagged from it, and `tailor.py` referenced it zero
times. So the loop could walk a page straight from too long to too short and
report success.

A page too short is not the lesser problem. Spilling is untidy; a sparse page
reads as a candidate with little to show, which is the impression the whole
selection exists to prevent.
"""
from __future__ import annotations

from job_os.services.resume_writing import MIN_PAGE_BULLETS, printed_bullets


def page(*bullet_counts: int) -> dict:
    """A document whose projects carry the given numbers of bullets."""
    return {
        "work": [{"position": "Engineer", "highlights": ["Did a thing."] * 4}],
        "projects": [
            {"name": f"Project {i}", "highlights": ["Built a thing."] * n}
            for i, n in enumerate(bullet_counts)
        ],
    }


def test_the_count_the_cut_asks_is_the_count_the_flag_asks() -> None:
    """One helper, so the two cannot drift into disagreeing."""
    assert printed_bullets(page(3, 1, 1)) == 9


def test_the_real_run_that_found_this() -> None:
    """Nine bullets, minus the three-bullet project, is six. Below the floor."""
    before = page(3, 1, 1)
    assert printed_bullets(before) == MIN_PAGE_BULLETS
    after = page(1, 1)
    assert printed_bullets(after) == 6
    assert printed_bullets(after) < MIN_PAGE_BULLETS


def test_cutting_a_small_project_can_still_be_safe() -> None:
    """The guard is about what the page has left, not about refusing to cut."""
    assert printed_bullets(page(3, 3, 3)) == 13
    assert printed_bullets(page(3, 3)) == 10
    assert printed_bullets(page(3, 3)) >= MIN_PAGE_BULLETS


def test_volunteer_bullets_count_toward_the_floor() -> None:
    """They render, so they hold the page up the same as any other bullet."""
    document = page(1, 1)
    document["volunteer"] = [{"position": "Mentor", "highlights": ["Taught."] * 3}]
    assert printed_bullets(document) == 9


def test_an_empty_highlight_is_not_a_bullet() -> None:
    document = page(1)
    document["projects"][0]["highlights"] = ["Built a thing.", "", None]
    assert printed_bullets(document) == 5
