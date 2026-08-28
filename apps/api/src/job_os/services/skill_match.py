"""Deterministic skill matching, shared by the scorer and the tailoring coverage pass.

Two places decide whether a candidate has a skill a posting asks for, and until
this module existed they decided it differently:

- `job_match._match_requirement` compared canonical keys, so it inherited every
  alias in `SKILL_ALIASES` for free ("k8s" and "Kubernetes" already matched).
- `tailor._requirement_coverage` searched the resume text for the posting's
  literal wording, so it inherited none of them. A vault that said "k8s" against
  a posting that said "Kubernetes" read as missing evidence -- and because that
  coverage feeds `_achievable_ats_score`, a miss there lowered the run's own
  target and produced a gap question for a skill the candidate demonstrably had.

No LLM, no network, no I/O, no model weights. `job_match` runs against the whole
posting corpus on every search, and a lookup that could block is not something it
can afford; that constraint is what rules embeddings out of this file, not a
judgement that they would score worse.
"""
from __future__ import annotations

from job_os.schemas.enrichment import SKILL_ALIASES, canonical_skill


def _build_variant_groups() -> dict[str, tuple[str, ...]]:
    """Canonical key -> every surface form in the table that resolves to it.

    The inverse of the lookup `canonical_skill` uses. `canonical_skill` answers
    "what do these two strings have in common"; this answers "what else could
    this skill be written as", which is the question a text search has to ask.
    """
    groups: dict[str, set[str]] = {}
    for surface, canon in SKILL_ALIASES.items():
        groups.setdefault(canon, set()).update((surface, canon))
    return {canon: tuple(sorted(forms)) for canon, forms in groups.items()}


_VARIANT_GROUPS = _build_variant_groups()


def alias_variants(term: str) -> tuple[str, ...]:
    """`term` first, then every other way the alias table spells the same skill.

    `term` leads so a caller that stops at the first hit reports the posting's
    own wording rather than a synonym, which is what the coverage citations show
    the user. Unknown terms come back as just themselves, so this is always safe
    to wrap around a term of any provenance.
    """
    canonical = canonical_skill(term)
    if not canonical:
        return (term,)
    others = (form for form in _VARIANT_GROUPS.get(canonical, ()) if form != term)
    return tuple(dict.fromkeys((term, *others)))


def satisfies(requirement: str, candidate: str) -> bool:
    """Whether one canonical candidate skill answers one canonical requirement.

    BOTH arguments must already be `canonical_skill` output; passing raw surface
    forms silently under-matches, which is the bug this module exists to stop.

    Three ways to satisfy, in order:

    1. Equality. The alias table has already collapsed "k8s"/"Kubernetes" and
       "postgres"/"PostgreSQL" by this point, so most real matches land here.

    2. A compound requirement fully containing the candidate's tokens. Real
       sources name compound requirements -- "Cloud Computing AWS" is satisfied
       by "aws". Tokenized, not substring, so "java" does not satisfy
       "javascript framework".

    3. A MORE SPECIFIC candidate satisfying a less specific requirement:
       "machine learning engineering" answers a "machine learning" ask. This
       direction was missing, and its absence inverted the scorer -- naming a
       skill more precisely than the posting did made it stop counting.

    Case 3 is deliberately gated on a requirement of two or more tokens. Opening
    it to single-token requirements would let any candidate skill containing that
    word satisfy it, and the words that appear alone in a requirement list are
    the generic ones: "penetration testing" would answer "testing", "social
    security" would answer "security", "graphic design" would answer "design". A
    false positive here inflates a score the user is trusting, which is worse
    than the miss it would fix, so single-token requirements still match by
    equality only -- which, through the alias table, is already most of them.
    """
    if requirement == candidate:
        return True
    requirement_tokens = frozenset(requirement.split(" "))
    candidate_tokens = frozenset(candidate.split(" "))
    if len(requirement_tokens) >= 2 and candidate_tokens <= requirement_tokens:
        return True
    return len(requirement_tokens) >= 2 and requirement_tokens <= candidate_tokens


def known_skill_terms() -> tuple[str, ...]:
    """Every skill name the alias table knows, longest first.

    For scanning free text (a resume bullet) for skills, rather than comparing
    two strings that are already known to be skills. Longest first so a scan
    that credits the first hit credits "amazon web services" rather than
    stopping at a shorter overlapping term.

    Deliberately only the names in the table. Harvesting every noun in a bullet
    would inflate every score at once, and the table is the closest thing this
    codebase has to a list of things someone already decided were skills.
    """
    names: set[str] = set(SKILL_ALIASES)
    names.update(SKILL_ALIASES.values())
    return tuple(sorted(names, key=lambda name: (-len(name), name)))
