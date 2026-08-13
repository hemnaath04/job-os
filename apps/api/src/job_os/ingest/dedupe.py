"""Two-stage duplicate detection.

The approach and both constants come from JobFunnel's `filters.py`, which is the
best-documented open-source treatment of this problem:

    MAX_TFIDF_SIMILARITY = 0.75
    MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH = 25

**Stage one, exact key.** Fold company, title and location and compare. This is
where most duplicates die, for two reasons that both come from how employers
actually file requisitions: one opening posted once per office becomes several
requisitions with identical text, and the same role appears on both a company
board and an aggregator. A hash comparison catches all of that.

**Stage two, TF-IDF cosine over descriptions.** Catches the pairs stage one
cannot, because stage one's key is an ordered string that also pins the location:
the same opening filed once per office, or retitled with the words reordered
("Lakebase Sales Specialist" / "Lakebase Specialist Sales"), repunctuated
("Solutions Architect (Lakebase)" / "Solutions Architect - Lakebase"), or with a
grade abbreviated ("Senior Staff Software Engineer - Backend" / "Sr Staff Software
Engineer (Backend)"). All three of those are real pairs this stage found in a
measured sweep and stage one missed.

0.75 cosine is necessary but not sufficient. It is gated on the two postings
naming the same role, for a reason the measurements below make concrete.

**Why the 25-row floor.** IDF is estimated from the corpus in front of it. Job
descriptions share enormous boilerplate, so in a small corpus almost every term
looks common, IDF flattens, and every pair scores high; the filter then merges
unrelated jobs. Below 25 rows the similarity number is noise and stage two is
skipped rather than trusted. That is JobFunnel's reasoning and it holds here.

**How the gates got here.** None of this was the first design. Each step below was
forced by inspecting what the previous one merged, on one real 300-board sweep
producing 19,461 postings, scored over the same 5,000-row candidate set each time:

    gate added                              marked / 5000   comparisons
    global IDF, first 400 tokens             1830 (36.6%)     2,620,224
    + company blocks, block-local IDF         875 (17.5%)       581,051
    + max_df boilerplate removal              829 (16.6%)       586,014
    + role gate, grades collapsed             405  (8.1%)           934
    + role gate, grades preserved             355  (7.1%)           597

Global IDF gets this backwards. A company's "About us" and benefits boilerplate
appears in every one of its postings and almost nowhere else in the corpus, so
globally it looks *rare* and IDF gives it a high weight. The vectors then measure
"are these from the same company", and for a pair from the same company that is
always yes. It merged "Electromechanical Assembly Technician" into "Mechanical
Assembly Technician" at 0.998.

Per-company IDF plus `max_df` was still not enough on heavily templated employers:
Anduril's postings stayed above 0.75 against each other, merging "Laser Test
Engineer" into "Manufacturing Test Engineer" at 0.754. Description similarity
alone cannot separate two jobs that share 80% of their text, so the role a title
names became a hard gate rather than a signal.

At the last step every remaining cross-title merge was checked by hand and all of
them were genuine. The two axes are independent and both necessary: blocking says
who could be the same job, the cosine says whether they are.

The implementation is deliberately dependency-free. scikit-learn would do this in
four lines, but it is a 30MB+ transitive install this API does not otherwise need,
and the maths for a sparse cosine over a few thousand short documents is a page.
`normalize.tokens_for_similarity` does the stopword removal that keeps ordinary
English from dominating the vectors.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from job_os.ingest.normalize import tokens_for_similarity

#: Cosine similarity at or above which two descriptions are the same posting.
MAX_TFIDF_SIMILARITY = 0.75
#: Below this many candidates in a block, IDF is not estimable and stage two is
#: skipped for that block.
MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH = 25
#: Tokens per description that feed the vectors. `jd_clean` is capped at 12,000
#: characters, roughly 1,800 content tokens, so this covers nearly all of a
#: typical posting. An earlier 400-token cap read only the head of the
#: description, which for most employers is the company intro, so every posting at
#: one company looked identical.
SIMILARITY_TOKEN_LIMIT = 1_500

#: Drop any term appearing in more than this fraction of a company's postings.
#: This is scikit-learn's `max_df`, and it is the parameter that actually fixes
#: the boilerplate problem. Down-weighting a shared term via IDF is not enough
#: when the shared text is most of the document: after L2 normalization those
#: terms still supply most of the dot product. Measured on a real 300-board sweep,
#: block-local IDF alone still merged "Laser Test Engineer" into "Manufacturing
#: Test Engineer" at 0.79. Removing the near-universal terms outright leaves the
#: comparison looking at the part of the posting that describes the role.
MAX_BLOCK_DOCUMENT_FREQUENCY = 0.6
#: A posting with fewer distinctive terms than this, once its company's shared
#: template is removed, has nothing left to compare. It is excluded from stage two
#: rather than scored on noise.
MIN_DISTINCTIVE_TOKENS = 8

#: Alternative spellings of the same seniority grade, folded to one token.
#:
#: Note what this does NOT do: it does not erase the grade. An earlier version
#: stripped every level word, which made "Senior Software Engineer - Database
#: Engine Internals" and "Staff Software Engineer - Database Engine Internals"
#: merge at 0.978, and those are two separate openings with two separate pay
#: bands. Collapsing them hides a job the user might be a better fit for, and
#: unlike a visible duplicate that is a loss they cannot see. Costs are asymmetric,
#: so the grade is preserved and only its spelling is canonicalized.
_LEVEL_SYNONYMS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "entry": "junior",
    "grad": "junior",
    "graduate": "junior",
    "assoc": "associate",
    "mgr": "manager",
    "eng": "engineer",
    "engr": "engineer",
    "swe": "software engineer",
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "l1": "1",
    "l2": "2",
    "l3": "3",
    "l4": "4",
    "l5": "5",
}


@dataclass(slots=True)
class DedupeCandidate:
    """Minimal view of a posting for dedupe. Keyed by whatever the caller uses."""

    key: str
    dedupe_key: str
    content_hash: str
    description: str
    #: Higher wins a tie. The worker passes a freshness score so the survivor of
    #: a duplicate pair is the one whose posting date we know best.
    rank: float = 0.0


@dataclass(slots=True)
class DuplicateLink:
    duplicate: str
    canonical: str
    reason: str
    score: float | None = None


@dataclass(slots=True)
class DedupeReport:
    links: list[DuplicateLink] = field(default_factory=list)
    exact_matches: int = 0
    similarity_matches: int = 0
    similarity_ran: bool = False
    comparisons: int = 0

    @property
    def duplicate_keys(self) -> set[str]:
        return {link.duplicate for link in self.links}


def find_duplicates(
    candidates: list[DedupeCandidate],
    *,
    max_similarity: float = MAX_TFIDF_SIMILARITY,
    min_for_similarity: int = MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH,
) -> DedupeReport:
    """Group `candidates` and report which are duplicates of which.

    Never merges transitively: a row already marked as a duplicate is not itself
    used as a canonical target, so a chain of near-matches cannot collapse ten
    distinct jobs into one through nine pairwise steps.
    """
    report = DedupeReport()
    if len(candidates) < 2:
        return report

    # Survivor selection is explicit rather than incidental. Highest rank wins,
    # and the key breaks ties so the same input always produces the same output.
    ordered = sorted(candidates, key=lambda c: (-c.rank, c.key))
    duplicates: set[str] = set()

    # --- stage one: identical content hash, then identical identity key -------
    by_hash: dict[str, str] = {}
    by_identity: dict[str, str] = {}
    for candidate in ordered:
        winner = by_hash.get(candidate.content_hash)
        if winner is not None:
            duplicates.add(candidate.key)
            report.links.append(
                DuplicateLink(candidate.key, winner, reason="content_hash")
            )
            report.exact_matches += 1
            continue
        winner = by_identity.get(candidate.dedupe_key)
        if winner is not None:
            duplicates.add(candidate.key)
            report.links.append(
                DuplicateLink(candidate.key, winner, reason="exact_key")
            )
            report.exact_matches += 1
            continue
        by_hash[candidate.content_hash] = candidate.key
        by_identity[candidate.dedupe_key] = candidate.key

    # --- stage two: TF-IDF cosine, per company block -------------------------
    survivors = [c for c in ordered if c.key not in duplicates and c.description.strip()]
    blocks: dict[str, list[DedupeCandidate]] = defaultdict(list)
    for candidate in survivors:
        blocks[_company_block(candidate)].append(candidate)

    for block in blocks.values():
        if len(block) < min_for_similarity:
            # Not enough documents in this block for IDF to mean anything.
            # Skipping is the correct answer, not a limitation to apologize for:
            # a fabricated similarity score would merge unrelated jobs.
            continue
        report.similarity_ran = True
        _dedupe_block(
            block,
            duplicates=duplicates,
            report=report,
            max_similarity=max_similarity,
        )

    return report


def _company_block(candidate: DedupeCandidate) -> str:
    """The company component of the dedupe key.

    `dedupe_key` is "company|title|location", and the company part is already
    normalized to a domain where one is known and a folded name otherwise, so it
    is the right blocking key without recomputing anything.
    """
    return candidate.dedupe_key.split("|", 1)[0]


def role_key(candidate: DedupeCandidate) -> frozenset[str]:
    """The role a title names, spelling normalized, grade preserved.

    Read from the dedupe key's title component so it cannot drift from the
    normalization the key already applied. A set rather than a string, so word
    order and punctuation stop mattering: "Warfighter Systems - Technical Writer"
    and "Technical Writer, Warfighter Systems" are one role advertised twice, and a
    string comparison misses it. That is the gap over stage one, whose key is an
    ordered string that also pins the location.
    """
    parts = candidate.dedupe_key.split("|")
    title = parts[1] if len(parts) > 1 else ""
    words: set[str] = set()
    for word in title.split():
        words.update(_LEVEL_SYNONYMS.get(word, word).split())
    return frozenset(words)


def _dedupe_block(
    block: list[DedupeCandidate],
    *,
    duplicates: set[str],
    report: DedupeReport,
    max_similarity: float,
) -> None:
    vectors = _tfidf_vectors(block)
    roles = [role_key(c) for c in block]
    # Candidates grouped by the role their title names. Two postings can only be
    # the same job if they name the same role, so this is both the correctness gate
    # and the thing that makes the pass cheap: comparisons happen inside a role
    # group, never across the whole board.
    by_role: dict[frozenset[str], list[int]] = defaultdict(list)
    for index, role in enumerate(roles):
        if role and vectors[index]:
            by_role[role].append(index)

    for indices in by_role.values():
        if len(indices) < 2:
            continue
        for position, i in enumerate(indices):
            if block[i].key in duplicates:
                continue
            for j in indices[position + 1 :]:
                other = block[j]
                if other.key in duplicates:
                    continue
                report.comparisons += 1
                score = _cosine(vectors[i], vectors[j])
                if score >= max_similarity:
                    duplicates.add(other.key)
                    report.links.append(
                        DuplicateLink(
                            other.key,
                            block[i].key,
                            reason="tfidf_cosine",
                            score=round(score, 4),
                        )
                    )
                    report.similarity_matches += 1


def _tfidf_vectors(candidates: list[DedupeCandidate]) -> list[dict[str, float]]:
    """L2-normalized TF-IDF vectors, so a cosine is a plain dot product.

    IDF is estimated over exactly the list passed in. Callers pass one company's
    postings, which is what makes that company's shared boilerplate score as
    common and drop out of the comparison. Passing a whole mixed corpus here
    would restore the failure mode described in the module docstring.
    """
    token_lists = [
        tokens_for_similarity(c.description)[:SIMILARITY_TOKEN_LIMIT] for c in candidates
    ]
    doc_count = len(token_lists)
    document_frequency: Counter[str] = Counter()
    for tokens in token_lists:
        document_frequency.update(set(tokens))

    # The company's shared template, discovered from the data rather than guessed.
    # Anything this common inside one company's postings is boilerplate by
    # definition and cannot help tell two of its postings apart.
    cutoff = MAX_BLOCK_DOCUMENT_FREQUENCY * doc_count
    boilerplate = {token for token, df in document_frequency.items() if df > cutoff}

    vectors: list[dict[str, float]] = []
    for tokens in token_lists:
        counts = Counter(t for t in tokens if t not in boilerplate)
        if len(counts) < MIN_DISTINCTIVE_TOKENS:
            # Nothing left but the template. An empty vector scores zero against
            # everything, which is the honest answer: we cannot tell.
            vectors.append({})
            continue
        vector: dict[str, float] = {}
        for token, count in counts.items():
            # Smoothed IDF, matching scikit-learn's default so the 0.75 threshold
            # keeps the meaning it has in JobFunnel.
            idf = math.log((1 + doc_count) / (1 + document_frequency[token])) + 1.0
            vector[token] = (1.0 + math.log(count)) * idf
        norm = math.sqrt(sum(w * w for w in vector.values()))
        vectors.append({t: w / norm for t, w in vector.items()} if norm else {})
    return vectors


def _rarest(vector: dict[str, float], *, limit: int) -> list[str]:
    """Highest-weight terms, which after IDF are the most distinctive ones."""
    return [t for t, _ in sorted(vector.items(), key=lambda kv: -kv[1])[:limit]]


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(weight * right.get(token, 0.0) for token, weight in left.items())
