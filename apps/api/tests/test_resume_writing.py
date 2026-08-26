"""The writing rules the tailored resume is held to.

Every case here is something a real run produced against the user's own profile,
so these tests are a record of what went out the door before they existed.
"""
from __future__ import annotations

from job_os.services.resume_writing import (
    bullet_flags,
    dedupe_bullets,
    document_quality_flags,
    estimated_page_lines,
    normalize_dashes,
    section_flags,
    similarity,
)

# The real pair: one EPAM fact and its re-imported twin, describing the same
# AI-agent work in different words. Both reached the page.
AGENT_LONG = (
    "In the latter half of the role, was part of a team building an AI agent "
    "that generates test cases directly from user stories, SRS, and FSDs, built "
    "on EPAM's internal LLM and in-house agent-creation platform. Demoed "
    "end-to-end; pending senior approval at the time I left."
)
AGENT_SHORT = (
    "Worked on a team building an AI agent that generates test cases from user "
    "stories, SRS, and FSDs on EPAM's internal LLM platform; demoed end-to-end."
)


def test_a_reworded_bullet_is_recognised_as_the_same_accomplishment() -> None:
    assert similarity(AGENT_LONG, AGENT_SHORT) > 0.6


def test_two_wordings_of_one_accomplishment_collapse_to_the_richer_one() -> None:
    kept = dedupe_bullets([AGENT_SHORT, AGENT_LONG])
    assert kept == [AGENT_LONG]


def test_distinct_accomplishments_both_survive() -> None:
    kept = dedupe_bullets(
        [
            "Migrated legacy suites to Cucumber and TestNG and tightened Jenkins CI/CD.",
            "Scored 2,404 sewer segments for dig-readiness by fusing scan data with GIS.",
        ]
    )
    assert len(kept) == 2


def test_em_dashes_leave_the_document_without_stray_punctuation() -> None:
    assert (
        normalize_dashes("Northeastern University — Khoury College")
        == "Northeastern University, Khoury College"
    )
    # A title reads better with a colon, which is how the user writes it.
    assert (
        normalize_dashes("BedRocked — Civic Sewer-Sequencing Platform", separator=": ")
        == "BedRocked: Civic Sewer-Sequencing Platform"
    )
    # The template's own replace produced "FSDs , built on"; a comma followed by
    # punctuation must not survive either.
    assert normalize_dashes("shipped it — . done") == "shipped it. done"
    # Hyphens inside words are not dashes and must be left alone.
    assert normalize_dashes("cents-per-asset inference at 0-100") == (
        "cents-per-asset inference at 0-100"
    )
    assert normalize_dashes(None) is None


def test_the_real_overlong_first_person_bullet_is_flagged() -> None:
    flags = bullet_flags(AGENT_LONG)
    assert any(flag.startswith("too_long") for flag in flags)
    assert "first_person" in flags
    assert "weak_opener" in flags


def test_jd_padding_is_caught_only_when_the_evidence_lacks_it() -> None:
    source = (
        "Ran daily root-cause analysis with developers on failing tests, raising "
        "coverage on the pricing engine."
    )
    padded = (
        "Ran daily root-cause analysis directly with engineers and product owners "
        "on failing tests, raising pricing-engine coverage and shortening "
        "time-to-fix on regressions in a fast-paced environment."
    )
    flags = bullet_flags(padded, source_text=source)
    assert any(flag.startswith("jd_padding") for flag in flags)
    assert any(flag.startswith("inflated_rewrite") for flag in flags)
    # The same phrase already in the evidence is the candidate's own wording, so
    # keeping it is not stuffing.
    assert not any(
        flag.startswith("jd_padding")
        for flag in bullet_flags(padded, source_text=padded)
    )


def test_a_good_bullet_has_nothing_to_say_about_it() -> None:
    assert bullet_flags(
        "Migrated legacy suites to Cucumber and TestNG and tightened Jenkins "
        "CI/CD, cutting flaky failures."
    ) == []


def test_three_bullets_opening_with_built_are_flagged() -> None:
    flags = section_flags(
        [
            "Built a dig-readiness score for 2,404 sewer segments.",
            "Built a parallel fetcher with atomic MongoDB worker claims.",
            "Hardened the FastAPI backend behind nginx and TLS.",
        ]
    )
    assert any(flag.startswith("repeated_opening_verb") for flag in flags)


def test_two_bullets_closing_the_same_way_are_flagged() -> None:
    """Neither is a duplicate of the other, but the shared clause reads as machine work.

    The real pair: an EPAM role whose second and third bullets both ended
    "adding regression coverage as pricing rules shipped".
    """
    flags = section_flags(
        [
            "Worked on the Fares team's Go test suite, triaging daily failures and "
            "adding regression coverage as pricing rules shipped.",
            "Migrated legacy suites to Cucumber and TestNG and tightened Jenkins "
            "CI/CD, adding regression coverage as pricing rules shipped.",
        ]
    )
    assert any(flag.startswith("repeated_phrase") for flag in flags)


def test_bullets_that_merely_share_vocabulary_are_not_flagged() -> None:
    assert not any(
        flag.startswith("repeated_phrase")
        for flag in section_flags(
            [
                "Wrote automated tests for a Go pricing engine.",
                "Migrated legacy suites to Cucumber and TestNG.",
            ]
        )
    )


def test_a_role_with_seven_bullets_is_flagged_where_it_lives() -> None:
    document = {
        "work": [
            {
                "name": "EPAM Systems",
                "position": "Software Test Automation Engineer",
                "highlights": [AGENT_LONG, AGENT_SHORT] + [
                    f"Wrote suite number {n}." for n in range(5)
                ],
            }
        ]
    }
    flags = document_quality_flags(document)
    key = "work: Software Test Automation Engineer"
    assert key in flags
    assert "near_duplicate_bullets" in flags[key]
    assert any(flag.startswith("too_many_bullets") for flag in flags[key])


def test_a_clean_document_reports_nothing() -> None:
    """Clean writing AND a complete page.

    This fixture carries basics, education and links because the reader checks
    treat their absence as a defect. Before those existed the same document
    passed while having no graduation date and no clickable link, which is
    exactly the resume the session notes describe getting screened out.
    """
    document = {
        "basics": {
            "profiles": [
                {"network": "GitHub", "url": "https://github.com/hemnaath04"},
                {"network": "LinkedIn", "url": "https://linkedin.com/in/hemnaath"},
            ]
        },
        "education": [
            {"institution": "Northeastern University", "endDate": "2028-05"}
        ],
        "work": [
            {
                "position": "Engineer",
                "highlights": [
                    "Migrated legacy suites to Cucumber and TestNG.",
                    "Wrote automated tests for a Go pricing engine across 6 markets.",
                    "Investigated failing tests daily with developers.",
                    "Trained new joiners on the internal tooling.",
                ],
            }
        ],
        "projects": [
            {
                "name": "One",
                "highlights": [
                    "Shipped a scheduler.",
                    "Tuned the cache eviction policy.",
                    "Designed the retry semantics.",
                ],
            },
            {
                "name": "Two",
                "highlights": ["Wrote the parser.", "Deployed behind nginx."],
            },
        ],
        # Go, not Python: no bullet here demonstrates Python, and claiming a
        # language the page never shows is the "listed without showing how it
        # was used" gap the reader checks now catch.
        "skills": [{"name": "Languages", "keywords": ["Go"]}],
    }
    assert document_quality_flags(document) == {}


def test_a_resume_that_stops_short_of_the_page_is_flagged() -> None:
    """Page fill varied run to run while it lived only in the prompt.

    One pass selected three projects and eight bullets, the next two projects and
    six, and the six-bullet resume ended a third of the way up the page.
    """
    thin = {
        "work": [{"position": "Engineer", "highlights": ["Wrote the parser."]}],
        "projects": [{"name": "One", "highlights": ["Shipped a scheduler."]}],
    }
    assert "page" in document_quality_flags(thin)
    assert document_quality_flags(thin)["page"] == ["thin_page(2 bullets)"]


def test_a_full_page_is_not_flagged_as_thin() -> None:
    document = {
        "work": [
            {
                "position": "Engineer",
                # Distinct openers: the page-wide opener check is real, and a
                # fixture that says "Did" four times would trip it while
                # testing something else entirely.
                "highlights": [
                    f"{verb} distinct thing number {n}."
                    for n, verb in enumerate(("Wrote", "Shipped", "Traced", "Cut"))
                ],
            }
        ],
        "projects": [
            {"name": "One", "highlights": [f"Built subsystem {n}." for n in range(3)]},
            {"name": "Two", "highlights": ["Wrote the parser.", "Tuned the cache."]},
        ],
    }
    assert "page" not in document_quality_flags(document)


def test_a_resume_that_spills_onto_a_second_page_is_flagged() -> None:
    """A page that overflows is not a fuller resume, it is a two-page one.

    Counting bullets cannot see this coming, which is how a real tailored run
    shipped two pages while passing every other check: 11 bullets over 4 entries
    rendered to two pages where 10 over 3 had fitted on one. The budget is
    measured in estimated rendered lines and calibrated against Tectonic.
    """
    # A distinct opening verb per bullet, so this stays a test about length. The
    # filler is padded to keep the word counts, and so the line arithmetic,
    # exactly as they were.
    verbs = iter(
        [
            "Wrote", "Shipped", "Traced", "Cut", "Tuned", "Built", "Drove",
            "Ported", "Split", "Cached", "Logged", "Merged", "Pruned",
        ]
    )
    def two_rows() -> str:
        return " ".join([next(verbs), *["word"] * 25])

    # Four entry headings, twelve two-row bullets and one three-row bullet: 31.
    document = {
        "work": [
            {
                "position": "Engineer",
                "highlights": [
                    " ".join([next(verbs), *["word"] * 26]),
                    *[two_rows() for _ in range(3)],
                ],
            }
        ],
        "projects": [
            {"name": str(n), "highlights": [two_rows() for _ in range(3)]}
            for n in range(3)
        ],
    }
    assert document_quality_flags(document)["page"] == ["over_page(31 of 30 lines)"]


def test_the_line_estimate_counts_the_entry_heading_and_the_wrap() -> None:
    single = {"work": [{"position": "Engineer", "highlights": ["Wrote the parser."]}]}
    # One heading row plus one bullet row.
    assert estimated_page_lines(single) == 2
    wrapped = {
        "work": [{"position": "Engineer", "highlights": [" ".join(["word"] * 27)]}]
    }
    # 27 words wrap onto three rows at 13 words each, under the same heading.
    assert estimated_page_lines(wrapped) == 4


# ---------------------------------------------------------------------------
# Reader-side checks, from the NVIDIA resume session (August 2026). These are
# not writing rules; they are the facts the three readers look for and the ways
# a well-written page can still fail to answer them.
# ---------------------------------------------------------------------------

# Minimal page that passes the reader checks, so each test below can break one
# thing and assert on that thing alone.
def _sound_page() -> dict:
    return {
        "basics": {
            "profiles": [
                {"network": "GitHub", "url": "https://github.com/hemnaath04"},
                {"network": "LinkedIn", "url": "https://linkedin.com/in/hemnaath"},
            ]
        },
        "education": [
            {"institution": "Northeastern University", "endDate": "2028-05"}
        ],
        "work": [
            {
                "position": "Engineer",
                "highlights": ["Wrote a Python parser covering 40 cases."],
            }
        ],
        "skills": [{"name": "Languages", "keywords": ["Python"]}],
    }


def test_a_sound_page_raises_none_of_the_reader_flags() -> None:
    found = document_quality_flags(_sound_page())
    for key in ("education", "links", "skills", "impact"):
        assert key not in found, f"{key}: {found.get(key)}"


def test_a_year_only_graduation_date_is_flagged() -> None:
    """The most common screen-out in the session notes. "2028" does not say
    which hiring cycle he is available for."""
    document = _sound_page()
    document["education"] = [{"institution": "Northeastern", "endDate": "2028"}]
    assert document_quality_flags(document)["education"] == [
        "no_graduation_month_and_year"
    ]


def test_a_written_month_counts_as_a_graduation_date() -> None:
    document = _sound_page()
    document["education"] = [{"institution": "Northeastern", "endDate": "May 2028"}]
    assert "education" not in document_quality_flags(document)


def test_a_page_with_no_education_says_so() -> None:
    document = _sound_page()
    document["education"] = []
    assert document_quality_flags(document)["education"] == ["missing_education"]


def test_a_skill_no_bullet_demonstrates_is_flagged() -> None:
    """"Technologies listed without showing how they were used", verbatim from
    the session's list of gaps. A skills row is a claim; a bullet is evidence."""
    document = _sound_page()
    document["skills"] = [{"name": "Languages", "keywords": ["Python", "Rust"]}]
    assert document_quality_flags(document)["skills"] == [
        "unevidenced_skill(Rust)"
    ]


def test_a_skill_shown_in_a_project_name_counts_as_evidenced() -> None:
    document = _sound_page()
    document["skills"] = [{"name": "Tools", "keywords": ["Docker"]}]
    document["projects"] = [
        {"name": "Docker build cache", "highlights": ["Cut image size by half."]}
    ]
    assert "skills" not in document_quality_flags(document)


def test_a_missing_github_link_is_flagged() -> None:
    """Reviewers click links. A page naming GitHub projects with no URL asks the
    reader to go searching, which they will not do."""
    document = _sound_page()
    document["basics"]["profiles"] = [
        {"network": "LinkedIn", "url": "https://linkedin.com/in/hemnaath"}
    ]
    assert document_quality_flags(document)["links"] == ["no_github_link"]


def test_a_project_url_satisfies_the_github_link() -> None:
    document = _sound_page()
    document["basics"]["profiles"] = [
        {"network": "LinkedIn", "url": "https://linkedin.com/in/hemnaath"}
    ]
    document["projects"] = [
        {
            "name": "BedRocked",
            "url": "https://github.com/hemnaath04/bedrocked",
            "highlights": ["Scored 2,404 sewer segments."],
        }
    ]
    assert "links" not in document_quality_flags(document)


def test_a_page_with_no_numbers_anywhere_is_flagged() -> None:
    """Deliberately a page-level signal, not a per-bullet rule: requiring a
    number in every bullet would push the model to invent one, and a fabricated
    metric is the failure that collapses in the interview."""
    document = _sound_page()
    document["work"] = [
        {"position": "Engineer", "highlights": ["Wrote a parser for the config format."]}
    ]
    document["skills"] = [{"name": "Languages", "keywords": []}]
    assert document_quality_flags(document)["impact"] == ["no_quantified_bullets"]


def test_one_real_number_clears_the_quantification_flag() -> None:
    document = _sound_page()
    document["work"] = [
        {"position": "Engineer", "highlights": ["Cut flaky failures to 3 per week."]}
    ]
    document["skills"] = [{"name": "Languages", "keywords": []}]
    assert "impact" not in document_quality_flags(document)
