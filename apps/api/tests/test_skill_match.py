"""The shared skill matcher, and the Phase-1 false negatives it was built to fix.

Two components decide whether a candidate has a skill a posting asks for, and
they used to decide it differently: the scorer compared canonical keys and so
inherited every alias in the table, while the tailoring coverage pass searched
the resume for the posting's literal wording and inherited none of them. A vault
saying "k8s" against a posting saying "Kubernetes" was a match to one and a gap
question to the other.

Worth recording precisely, because the Phase 1 report got part of this wrong:
k8s/Kubernetes, postgres/PostgreSQL and AWS/Amazon Web Services were ALREADY
handled on the scorer's side by `SKILL_ALIASES`. They are pinned here as
regression guards, not as fixes. The real defects were the inverted containment
below, and the coverage pass having no alias support at all.
"""
from __future__ import annotations

from job_os.db.models.profile import FactBullet, ProfileFact
from job_os.schemas.enrichment import canonical_skill
from job_os.services.job_match import build_candidate_profile
from job_os.services.skill_match import alias_variants, known_skill_terms, satisfies


def canon(*names: str) -> list[str]:
    return [canonical_skill(name) for name in names]


# --- aliases that already worked: guard them ---------------------------------


def test_k8s_and_kubernetes_are_one_skill() -> None:
    requirement, candidate = canon("Kubernetes", "k8s")
    assert satisfies(requirement, candidate)


def test_postgres_and_postgresql_are_one_skill() -> None:
    requirement, candidate = canon("PostgreSQL", "postgres")
    assert satisfies(requirement, candidate)


def test_aws_and_amazon_web_services_are_one_skill() -> None:
    requirement, candidate = canon("AWS", "Amazon Web Services")
    assert satisfies(requirement, candidate)


# --- the inverted containment, which is the actual fix -----------------------


def test_a_more_specific_candidate_satisfies_a_less_specific_requirement() -> None:
    """The defect: naming a skill MORE precisely than the posting stopped it counting.

    "machine learning engineering" is strictly more than "machine learning", and
    the old rule only accepted a candidate whose tokens were a SUBSET of the
    requirement's, so the better answer scored as a miss.
    """
    requirement, candidate = canon("machine learning", "machine learning engineering")
    assert satisfies(requirement, candidate)


def test_a_compound_requirement_is_still_satisfied_by_its_part() -> None:
    """The direction that already worked has to survive the fix.

    Real sources carry compound requirements like "Cloud Computing AWS"; a
    candidate with AWS answers it.
    """
    requirement, candidate = canon("Cloud Computing AWS", "AWS")
    assert satisfies(requirement, candidate)


def test_java_still_does_not_satisfy_a_javascript_requirement() -> None:
    """The over-match guard the tokenizer exists for."""
    requirement, candidate = canon("javascript framework", "java")
    assert not satisfies(requirement, candidate)


def test_a_single_token_requirement_is_not_widened() -> None:
    """Deliberately conservative, and the one place I did not follow the brief.

    Opening the more-specific direction to single-token requirements would let
    "penetration testing" answer a "testing" ask, "social security" answer
    "security" and "graphic design" answer "design" -- the words that appear
    alone in a requirement list are the generic ones. A false positive inflates a
    score the user is trusting, which is worse than the miss it fixes.
    """
    requirement, candidate = canon("testing", "penetration testing")
    assert not satisfies(requirement, candidate)


def test_matching_is_reflexive() -> None:
    requirement = canonical_skill("Python")
    assert satisfies(requirement, requirement)


# --- what the tailoring coverage pass now gets -------------------------------


def test_alias_variants_offer_the_other_spellings() -> None:
    variants = alias_variants("Kubernetes")
    assert "kubernetes" in variants
    assert "k8s" in variants


def test_alias_variants_lead_with_the_postings_own_wording() -> None:
    """Coverage citations quote the posting, so its wording has to come first."""
    assert alias_variants("Kubernetes")[0] == "Kubernetes"


def test_an_unknown_term_comes_back_as_itself() -> None:
    assert alias_variants("Zorblatt Framework") == ("Zorblatt Framework",)


def test_known_skill_terms_are_longest_first() -> None:
    """So a bullet naming "amazon web services" is not credited only with "aws"."""
    terms = known_skill_terms()
    assert terms == tuple(sorted(terms, key=lambda name: (-len(name), name)))


# --- mining bullet text into the scored profile ------------------------------


def fact(kind: str, title: str, *, bullets: list[str] | None = None) -> ProfileFact:
    row = ProfileFact(kind=kind, title=title, payload={}, verified=True)
    row.bullets = [FactBullet(text=text) for text in (bullets or [])]
    return row


def test_a_skill_named_only_in_a_bullet_is_now_credited() -> None:
    """The gap: bullets are where the work is described, and the scorer was blind to them."""
    profile = build_candidate_profile(
        [fact("experience", "Engineer", bullets=["Built the retrieval service in FastAPI"])]
    )
    assert canonical_skill("FastAPI") in profile.skills


def test_a_bullet_skill_satisfies_a_posting_requirement() -> None:
    profile = build_candidate_profile(
        [fact("experience", "Engineer", bullets=["Ran the cluster on Kubernetes"])]
    )
    requirement = canonical_skill("k8s")
    assert any(satisfies(requirement, skill) for skill in profile.skills)


def test_an_unverified_fact_contributes_no_bullet_skills() -> None:
    """The no-hallucination rule: an unconfirmed draft is not evidence."""
    row = fact("experience", "Engineer", bullets=["Built it in FastAPI"])
    row.verified = False
    assert canonical_skill("FastAPI") not in build_candidate_profile([row]).skills


def test_bullet_prose_does_not_become_a_skill() -> None:
    """Only names the alias table already knows, or every noun inflates every score."""
    profile = build_candidate_profile(
        [fact("experience", "Engineer", bullets=["Reduced cost and improved morale"])]
    )
    assert "cost" not in profile.skills
    assert "morale" not in profile.skills


def test_a_word_inside_another_word_is_not_a_skill() -> None:
    """"Go" must not be found inside "MongoDB" -- the lesson `_mentions` records."""
    profile = build_candidate_profile(
        [fact("experience", "Engineer", bullets=["Stored the records in MongoDB"])]
    )
    assert canonical_skill("Go") not in profile.skills
