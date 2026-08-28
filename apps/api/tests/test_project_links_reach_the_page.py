"""A project with a verified link gets that link on its heading.

`ProfileFact` has exactly one URL column. Anything a resume import or a fact
edit supplied as a second link -- the repository next to the demo, the bot next
to the repository -- landed in `payload` and was never read again, so a project
whose link was recorded that way rendered with `url: null` and printed a heading
nobody could click. Every template hyperlinks the project name and prints
nothing when there is no link, so the omission was silent.

Reading the payload is the fix. Guessing is not: a fact holding "github.com/x/y"
with no scheme stays unlinked and becomes a gap card, because completing a URL
is inventing one.
"""
from __future__ import annotations

from job_os.services.resume_writing import unlinked_projects
from job_os.services.tailor import TailorFact, _project_url


def project(**payload: object) -> TailorFact:
    return TailorFact(id="p", kind="project", title="Recipe Swap", payload=dict(payload))


def test_the_column_is_used_when_it_has_one() -> None:
    fact = TailorFact(
        id="p",
        kind="project",
        title="Recipe Swap",
        source_url="https://example.com/recipe-swap",
    )
    assert _project_url(fact) == "https://example.com/recipe-swap"


def test_a_link_recorded_on_the_payload_reaches_the_heading() -> None:
    """The repro: the link was there the whole time, one field over."""
    assert _project_url(project(github="https://github.com/example/recipe-swap")) == (
        "https://github.com/example/recipe-swap"
    )


def test_a_demo_link_counts_when_there_is_no_repository() -> None:
    assert _project_url(project(demo="https://recipe-swap.example.com")) == (
        "https://recipe-swap.example.com"
    )


def test_the_repository_leads_when_a_fact_carries_several() -> None:
    # Only one link fits a heading. A reviewer opens the repository to check the
    # work, so that is the one worth the slot.
    fact = project(
        demo="https://recipe-swap.example.com",
        github="https://github.com/example/recipe-swap",
    )
    assert _project_url(fact) == "https://github.com/example/recipe-swap"


def test_the_column_still_wins_over_the_payload() -> None:
    fact = TailorFact(
        id="p",
        kind="project",
        title="Recipe Swap",
        source_url="https://example.com/canonical",
        payload={"github": "https://github.com/example/recipe-swap"},
    )
    assert _project_url(fact) == "https://example.com/canonical"


def test_a_url_with_no_scheme_is_not_completed() -> None:
    """Adding https:// in front of a string is guessing a URL."""
    assert _project_url(project(github="github.com/example/recipe-swap")) is None


def test_a_non_web_scheme_never_reaches_the_page() -> None:
    assert _project_url(project(demo="file:///Users/someone/recipe-swap")) is None


def test_a_project_with_nothing_recorded_stays_unlinked() -> None:
    assert _project_url(project()) is None


def test_the_page_names_the_projects_the_reader_cannot_click() -> None:
    document = {
        "projects": [
            {"name": "Recipe Swap", "url": "https://github.com/example/recipe-swap"},
            {"name": "Shift Planner", "url": None},
            {"name": "Reading List", "url": ""},
        ]
    }
    assert unlinked_projects(document) == ["Shift Planner", "Reading List"]


def test_a_page_where_every_project_links_reports_nothing() -> None:
    document = {"projects": [{"name": "Recipe Swap", "url": "https://example.com/rs"}]}
    assert unlinked_projects(document) == []
