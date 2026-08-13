"""Two-stage dedupe: the thresholds, and the gates that make them safe.

The thresholds come from JobFunnel's `filters.py`. The gates around them do not:
they were added because a global TF-IDF pass over a real 300-board crawl marked
1,830 of 5,000 postings as duplicates and merged genuinely different jobs. The
tests named `..._is_not_a_duplicate_of_...` are that regression.
"""
from __future__ import annotations

from job_os.ingest.dedupe import (
    MAX_TFIDF_SIMILARITY,
    MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH,
    DedupeCandidate,
    find_duplicates,
    role_key,
)
from job_os.ingest.normalize import content_hash, dedupe_key

COMPANY = "acme.com"


def candidate(
    key: str,
    *,
    title: str,
    location: str = "San Francisco, CA",
    description: str = "",
    company: str = "Acme",
    domain: str = COMPANY,
    rank: float = 0.0,
) -> DedupeCandidate:
    return DedupeCandidate(
        key=key,
        dedupe_key=dedupe_key(company, title, location, domain=domain),
        content_hash=content_hash(company, title, location, description, domain=domain),
        description=description,
        rank=rank,
    )


def filler(
    n: int, *, description: str, title_prefix: str = "Unrelated Role"
) -> list[DedupeCandidate]:
    """Distinct postings, purely to push a block past the similarity floor."""
    return [
        candidate(
            f"filler-{i}",
            title=f"{title_prefix} {i}",
            description=description + f" topic{i}",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# stage one: exact keys
# ---------------------------------------------------------------------------


def test_identical_content_hash_is_a_duplicate() -> None:
    a = candidate("a", title="Software Engineer", description="Same body")
    b = candidate("b", title="Software Engineer", description="Same body")
    report = find_duplicates([a, b])

    assert len(report.links) == 1
    assert report.exact_matches == 1
    assert report.links[0].reason == "content_hash"


def test_same_company_title_location_is_a_duplicate_even_with_different_bodies() -> None:
    """One requisition, two slightly different descriptions, is still one job."""
    a = candidate("a", title="Software Engineer", description="Body one is here.")
    b = candidate("b", title="Software Engineer", description="Body two differs a lot.")
    report = find_duplicates([a, b])

    assert len(report.links) == 1
    assert report.links[0].reason == "exact_key"


def test_different_locations_are_not_caught_by_stage_one() -> None:
    """Stage one pins the location, which is exactly why stage two exists."""
    a = candidate("a", title="Software Engineer", location="San Francisco, CA")
    b = candidate("b", title="Software Engineer", location="New York, NY")
    report = find_duplicates([a, b])
    assert report.exact_matches == 0


def test_different_companies_are_never_duplicates() -> None:
    a = candidate("a", title="Software Engineer", company="Acme", domain="acme.com")
    b = candidate("b", title="Software Engineer", company="Globex", domain="globex.com")
    assert find_duplicates([a, b]).links == []


def test_survivor_is_the_highest_ranked() -> None:
    """The worker passes a freshness rank so the row with the best date survives."""
    weak = candidate("weak", title="Engineer", description="Same", rank=0.0)
    strong = candidate("strong", title="Engineer", description="Same", rank=5.0)
    report = find_duplicates([weak, strong])

    assert report.links[0].canonical == "strong"
    assert report.links[0].duplicate == "weak"


def test_empty_and_single_inputs_are_safe() -> None:
    assert find_duplicates([]).links == []
    assert find_duplicates([candidate("a", title="Engineer")]).links == []


# ---------------------------------------------------------------------------
# stage two: the similarity floor
# ---------------------------------------------------------------------------


def test_similarity_is_skipped_below_the_minimum_job_count() -> None:
    """Below 25 documents, IDF is not estimable and the score would be noise.

    Skipping is the correct answer. Scoring anyway is how a small corpus merges
    unrelated jobs, which is the failure JobFunnel's constant guards against.
    """
    body = "Kubernetes Terraform platform reliability engineering on call rotations."
    few = [
        candidate(f"c{i}", title=f"Role {i}", location=f"City {i}", description=body)
        for i in range(MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH - 1)
    ]
    report = find_duplicates(few)

    assert report.similarity_ran is False
    assert report.similarity_matches == 0


def test_similarity_runs_at_the_minimum_job_count() -> None:
    body = "Kubernetes Terraform platform reliability engineering on call rotations."
    enough = [
        candidate(f"c{i}", title=f"Role {i}", location=f"City {i}", description=body + f" x{i}")
        for i in range(MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH)
    ]
    assert find_duplicates(enough).similarity_ran is True


def test_similarity_merges_the_same_role_in_two_locations() -> None:
    """The high-value catch: one opening filed once per office.

    Stage one misses it because its key pins the location.
    """
    body = (
        "Design and operate distributed storage systems. Own replication, "
        "compaction and repair. Work with Rust and Kubernetes at petabyte scale."
    )
    pair = [
        candidate("sf", title="Storage Engineer", location="San Francisco, CA", description=body),
        candidate("ny", title="Storage Engineer", location="New York, NY", description=body),
    ]
    report = find_duplicates(pair + filler(30, description="Completely different subject matter"))

    merged = [link for link in report.links if link.reason == "tfidf_cosine"]
    assert {link.duplicate for link in merged} == {"ny"} or {
        link.duplicate for link in merged
    } == {"sf"}
    assert merged[0].score is not None and merged[0].score >= MAX_TFIDF_SIMILARITY


def test_similarity_merges_a_reordered_title() -> None:
    """Observed pair: "Lakebase Sales Specialist" / "Lakebase Specialist Sales".

    The role key is a set, so word order stops mattering.
    """
    body = (
        "Sell the Lakebase product to enterprise accounts. Build pipeline, run "
        "discovery calls, partner with solutions architects on proofs of concept."
    )
    pair = [
        candidate("a", title="Lakebase Sales Specialist", location="Austin, TX", description=body),
        candidate("b", title="Lakebase Specialist Sales", location="Denver, CO", description=body),
    ]
    report = find_duplicates(pair + filler(30, description="Entirely separate discipline"))
    assert any(link.reason == "tfidf_cosine" for link in report.links)


def test_similarity_merges_an_abbreviated_grade() -> None:
    """Observed pair: "Senior Staff Software Engineer" / "Sr Staff Software Engineer"."""
    body = (
        "Lead backend architecture for the payments platform. Mentor engineers, "
        "own service level objectives, drive migrations across many teams."
    )
    pair = [
        candidate("a", title="Senior Staff Software Engineer Backend",
                  location="A, CA", description=body),
        candidate("b", title="Sr Staff Software Engineer Backend",
                  location="B, NY", description=body),
    ]
    report = find_duplicates(pair + filler(30, description="Nothing alike at all"))
    assert any(link.reason == "tfidf_cosine" for link in report.links)


# ---------------------------------------------------------------------------
# the regressions: what must NOT merge
# ---------------------------------------------------------------------------


def test_a_different_role_is_not_a_duplicate_despite_shared_boilerplate() -> None:
    """Measured regression.

    A global TF-IDF pass merged "Laser Test Engineer" into "Manufacturing Test
    Engineer" at 0.754 because the employer's postings share ~80% of their text.
    Both the role gate and per-company `max_df` exist to stop this.
    """
    boilerplate = (
        "About the company. We build advanced defense systems. Our benefits include "
        "health dental vision unlimited leave and equity. We are an equal opportunity "
        "employer committed to a diverse workforce. Apply today to join the mission. "
    ) * 6
    pair = [
        candidate(
            "laser",
            title="Laser Test Engineer",
            location="Costa Mesa, CA",
            description=boilerplate + "Characterize laser diodes and optical benches.",
        ),
        candidate(
            "mfg",
            title="Manufacturing Test Engineer",
            location="Costa Mesa, CA",
            description=boilerplate + "Build production test fixtures for assembly lines.",
        ),
    ]
    report = find_duplicates(pair + filler(30, description=boilerplate + "Third distinct topic"))

    merged = {link.duplicate for link in report.links}
    assert "laser" not in merged and "mfg" not in merged


def test_seniority_grades_are_kept_apart() -> None:
    """Senior and Staff are separate openings with separate pay bands.

    Collapsing them hides a job the user might be a better fit for, and unlike a
    visible duplicate that is a loss they cannot see.
    """
    body = (
        "Own the database engine internals. Query planning, vectorized execution, "
        "storage formats, and the optimizer. Deep C++ and systems background."
    )
    pair = [
        candidate("senior", title="Senior Software Engineer Database Engine",
                  location="A, CA", description=body),
        candidate("staff", title="Staff Software Engineer Database Engine",
                  location="A, CA", description=body),
    ]
    report = find_duplicates(pair + filler(30, description="Unrelated marketing content"))

    merged = {link.duplicate for link in report.links}
    assert "senior" not in merged and "staff" not in merged


def test_boilerplate_only_postings_are_not_compared() -> None:
    """With nothing but the company template, any score is meaningless."""
    template = "We are a company. We have benefits. We are an equal opportunity employer. " * 8
    rows = [
        candidate(f"c{i}", title=f"Role {i}", location=f"City {i}", description=template)
        for i in range(30)
    ]
    report = find_duplicates(rows)
    assert report.similarity_matches == 0


def test_dedupe_never_chains_transitively() -> None:
    """A row already marked a duplicate must not become someone else's canonical.

    Otherwise ten distinct jobs can collapse into one through nine pairwise steps.
    """
    body = "Identical body text for all three of these postings, word for word."
    # Same company, title and location, so all three collide on stage one and the
    # question is purely which one becomes canonical for the other two.
    rows = [
        candidate("a", title="Engineer", location="A, CA", description=body, rank=3.0),
        candidate("b", title="Engineer", location="A, CA", description=body, rank=2.0),
        candidate("c", title="Engineer", location="A, CA", description=body, rank=1.0),
    ]
    report = find_duplicates(rows)

    canonicals = {link.canonical for link in report.links}
    duplicates = {link.duplicate for link in report.links}
    assert canonicals == {"a"}
    assert duplicates == {"b", "c"}
    assert not (canonicals & duplicates)


# ---------------------------------------------------------------------------
# the role gate, directly
# ---------------------------------------------------------------------------
# Cosine similarity alone cannot separate two jobs that share 80% of their text,
# so the role a title names is a hard gate rather than another signal. These
# exercise it without going through a whole block, because when a merge above is
# wrong this is almost always the reason.


def test_role_key_canonicalizes_grade_spelling() -> None:
    """"Sr" and "Senior" are one grade spelled two ways."""
    assert role_key(candidate("a", title="Sr Software Engineer")) == role_key(
        candidate("b", title="Senior Software Engineer")
    )


def test_role_key_ignores_word_order_and_punctuation() -> None:
    """A set, not a string: "Warfighter Systems - Technical Writer" and
    "Technical Writer, Warfighter Systems" are one role advertised twice."""
    assert role_key(candidate("a", title="Warfighter Systems - Technical Writer")) == role_key(
        candidate("b", title="Technical Writer, Warfighter Systems")
    )


def test_role_key_keeps_the_grade_itself() -> None:
    """Spelling is normalized; the grade is not erased. Senior is not Staff."""
    assert role_key(candidate("a", title="Senior Software Engineer")) != role_key(
        candidate("b", title="Staff Software Engineer")
    )


def test_role_key_separates_different_disciplines() -> None:
    assert role_key(candidate("a", title="Technical Writer")) != role_key(
        candidate("b", title="Technical Program Manager")
    )


def test_the_similarity_floor_is_per_company_not_per_corpus() -> None:
    """IDF is estimated inside a company block, so the block is what must be big.

    A corpus far past the floor, made of small per-company blocks, still skips
    stage two. Counting the corpus instead would run the comparison on employers
    whose handful of postings cannot support it.
    """
    corpus: list[DedupeCandidate] = []
    for company_index in range(10):
        corpus += [
            candidate(
                f"c{company_index}-{i}",
                title=f"Role {i}",
                location=f"City {i}",
                company=f"Company {company_index}",
                domain=f"company{company_index}.com",
                description="Shared subject matter with a little variation here.",
            )
            for i in range(5)
        ]

    report = find_duplicates(corpus)

    assert len(corpus) > MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH
    assert report.similarity_ran is False


def test_output_does_not_depend_on_input_order() -> None:
    """Survivor selection is explicit, so the same input set gives one answer."""
    rows = [
        candidate("a", title="Engineer", description="Same body", rank=1.0),
        candidate("b", title="Engineer", description="Same body", rank=2.0),
    ]
    forward = find_duplicates(rows)
    backward = find_duplicates(list(reversed(rows)))

    assert [(link.duplicate, link.canonical) for link in forward.links] == [
        (link.duplicate, link.canonical) for link in backward.links
    ]


def test_threshold_constants_match_jobfunnel() -> None:
    """These are quoted from JobFunnel's filters.py; drifting silently is a bug."""
    assert MAX_TFIDF_SIMILARITY == 0.75
    assert MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH == 25


def test_comparison_count_stays_small() -> None:
    """Blocking is what makes this cheap.

    An unblocked pass over the same 5,000-row candidate set measured 2,620,224
    comparisons; blocked by company and role it measured 597.
    """
    rows = [
        candidate(
            f"c{i}",
            title=f"Role {i % 7}",
            location=f"City {i}",
            description=f"Body about subject {i % 7} with detail and specifics.",
        )
        for i in range(200)
    ]
    report = find_duplicates(rows)
    assert report.comparisons < 200 * 200 / 2
