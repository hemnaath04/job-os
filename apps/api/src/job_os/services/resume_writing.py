"""Deterministic resume-writing checks, shared by the tailor loop and the review.

Every rule here comes from the user's own career-ops benchmark rather than from
a model's taste: bullets read like an engineer explaining their work, one idea
each, one or two rendered lines, a strong past-tense opening verb, no first
person, no dash characters, no JD phrases pasted in to farm a keyword.

These functions never write new claims. They measure text and they choose
between wordings that already exist in the verified profile, which is what keeps
them usable inside the no-hallucination contract.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# One idea per bullet, one or two rendered lines. Past this, a bullet wraps to a
# third line in the Letter template and starts crowding out a whole other bullet.
BULLET_MAX_WORDS = 30
# A rewrite is allowed to reshape a bullet, not to inflate it. Growing a verified
# bullet by more than this is how JD padding gets in.
REWRITE_GROWTH_LIMIT = 1.15
# Below this the bullet is short enough that growing it is probably an
# improvement, so growth is only judged once the result is a real paragraph.
INFLATION_FLOOR_WORDS = 20

# Bullets per entry. The benchmark asks for two to four per role or project, and
# a role showing seven is the single loudest "unedited draft" signal.
MAX_WORK_BULLETS = 4
MAX_PROJECT_BULLETS = 3

# Two rendered lines of the tailored summary. Longer and it stops being a lede
# and starts being a paragraph the reader skips.
SUMMARY_MAX_WORDS = 45

# Bullets a full Letter page carries across roles and projects. Below this the
# resume stops a third of the way up the page, which reads as a thin candidate
# rather than a concise one. Asking for page fill in the prompt alone left it to
# vary run to run: one pass selected three projects and eight bullets, the next
# two projects and six. A resume cannot pad its way past this, because every
# bullet has to come from a verified fact, so the only way to satisfy it is to
# surface more real evidence.
MIN_PAGE_BULLETS = 9

# The other end of the same budget. Overflowing the page does not produce a
# fuller resume, it produces a two-page one, which the career-ops rules forbid
# outright.
#
# Recalibrated when `estimated_page_lines` was taught to count the whole page
# rather than just the roles and projects. The old 30 was measured against an
# estimate that ignored the summary, the education, the skills and the
# certificates, so it was a true number about the wrong quantity.
#
# Measured again on a real tailored document, trimmed section by section and
# rendered through Tectonic on three templates:
#
#   est 55  husky 2  jakes 2  sb2nov 2
#   est 51  husky 1  jakes 2  sb2nov 1
#   est 47  husky 1  jakes 1  sb2nov 1
#   est 42  husky 1  jakes 1  sb2nov 1
#
# 47 is the largest estimate measured to fit on every template, including jakes,
# which spills first. Going lower would cost page fill on documents that
# demonstrably fit.
MAX_PAGE_LINES = 47


@dataclass(frozen=True)
class PageShape:
    """What one page of a given template actually holds.

    The estimator used to model a generic resume and hand the same number to
    every template, which is wrong in both directions at once. Measured against
    real renders of one AMD co-op document: the six Typst templates carry 47
    comfortably and the tightest of them, jakes, only spills at 49, while husky
    goes to two pages somewhere above 42. So 47 was right for most of them and
    too generous for the one the co-op resume uses.

    `renders_summary` is the other half, and the more expensive mistake. husky
    has no resume-level summary section at all -- its sections are Education,
    Technical Skills, Professional Experience, Projects -- so counting summary
    lines toward its page, and then deleting the summary to save them, removes
    the candidate's opening paragraph and frees nothing. That happened on two
    real runs, and both still came out two pages.
    """

    max_lines: int
    renders_summary: bool = True


DEFAULT_PAGE_SHAPE = PageShape(max_lines=MAX_PAGE_LINES)

#: Measured by rendering the same document at a range of estimator values and
#: reading the page count back, per template. Anything absent takes the default.
PAGE_SHAPES: dict[str, PageShape] = {
    # Tectonic, Times at 11pt, and denser than the Typst set.
    "husky": PageShape(max_lines=41, renders_summary=False),
}


def page_shape(template_key: str | None) -> PageShape:
    """The page budget and section list for a template, or the default."""
    if not template_key:
        return DEFAULT_PAGE_SHAPE
    return PAGE_SHAPES.get(template_key, DEFAULT_PAGE_SHAPE)
# Words a bullet fits on one line in the one-page template.
WORDS_PER_RENDERED_LINE = 13

# Distinct skill rows a one-page resume can carry before the block eats the
# space that evidence should occupy. Six is what the real profile needs once
# duplicate categories are folded together; capping lower than that would force a
# correctly labelled row like "Infrastructure" into a generic bucket, which is
# worse than the extra line.
MAX_SKILL_GROUPS = 6

# Separator characters that have no business on an ATS-parsed page: em dash, en
# dash, double hyphen, and the middle dot and bullet that resume imports pick up
# from a PDF's own layout glyphs. The independent review flagged the middle dot in
# a job title as something an ATS parser can mangle.
_DASH_RE = re.compile(r"\s*(?:—|–|--|·|•|‧|∙)\s*")
_WORD_RE = re.compile(r"[a-z0-9+#.]+")

# Words that carry no meaning for a similarity test, so two bullets about the
# same work are not judged different just because one says "the" more.
_STOPWORDS = frozenset(
    ["a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "onto", "of", "on", "or", "over", "under", "the", "to", "with", "that", "which", "while", "across", "most", "new", "using", "via", "per", "each", "its", "their", "his", "her", "them", "it", "was", "were", "are", "not", "this", "these", "those", "also", "than", "then", "such", "same", "other", "more", "less", "many", "much", "some", "any", "all", "both", "own", "very", "just", "only", "after", "before", "during", "directly"]
)

# Padding a bullet with the JD's own soft-skill or culture wording is the failure
# mode the benchmark calls out by name. None of these describe work done.
_JD_PADDING_RE = re.compile(
    r"\b(?:"
    r"fast[- ]paced|fast[- ]moving|work ethic|team player|self[- ]starter|"
    r"detail[- ]oriented|passion(?:ate)? for|genuine interest|"
    r"cross[- ]functional environment|dynamic environment|"
    r"in a collaborative environment|"
    r"applying (?:core |computer science )?(?:fundamentals|data[- ]structures)|"
    r"appl(?:ying|ied) problem[- ]solving(?: and communication)?|"
    r"addressing \w+ considerations|"
    r"natural[- ]language,? prompt[- ]based generative[- ]ai application|"
    r"demonstrating|showcasing|underscoring|highlighting my|"
    r"strong grasp of|solid grasp of"
    r")\b",
    re.I,
)

# Promotional adjectives and AI-vocabulary verbs, from the benchmark ban list.
BANNED_WORDING = (
    "leveraged",
    "utilized",
    "spearheaded",
    "orchestrated",
    "empowered",
    "fostered",
    "streamlined",
    "synergized",
    "revolutionized",
    "transformed",
    "facilitated",
    "enabled",
    "delved",
    "underscored",
    "showcased",
    "cutting-edge",
    "state-of-the-art",
    "innovative",
    "robust",
    "foundational",
    "passionate",
    "seamlessly",
    "seamless",
    "comprehensive",
    "sophisticated",
    "holistic",
    "synergy",
    "meticulous",
    "pivotal",
)

# "end to end" and "end-to-end" both read as the same brochure phrase; matched
# separately from BANNED_WORDING's plain substring check because the hyphen is
# optional and a model writes it either way.
_END_TO_END_RE = re.compile(r"\bend[\s-]to[\s-]end\b", re.I)

# Openers that hedge away the candidate's own contribution. The benchmark's
# ownership rule cuts both ways: do not overclaim, but do not open a bullet with
# a phrase that says nothing either.
_WEAK_OPENER_RE = re.compile(
    r"^(?:in the |as part of|part of a team|was part of|was responsible|"
    r"responsible for|helped |assisted |participated in|involved in|"
    r"tasked with|had the opportunity)",
    re.I,
)

# Case is spelled out rather than using re.I on purpose: "US" is a market, not a
# pronoun, and a case-insensitive "us" would flag "US markets" on every resume
# that mentions one.
_FIRST_PERSON_RE = re.compile(
    r"\b(?:I|I'?m|I'?ve|[Mm]y|[Mm]e|[Mm]ine|[Ww]e|[Ww]e'?ve|[Oo]ur|[Oo]urs|us)\b"
)

# Wording that says a thing reached users or production, in terms that can only
# be about the work itself. Present tense included on purpose: a summary reading
# "ships production FastAPI systems and agentic AI workflows" makes exactly the
# claim a past-tense-only pattern let through.
_COMPLETION_RE = re.compile(
    r"\b(?:"
    r"ships?|shipped|shipping|"
    r"launch(?:es|ed|ing)|"
    r"releas(?:es|ed)|"
    r"deliver(?:s|ed|ing)|"
    r"roll(?:s|ed|ing) out|"
    r"(?:go(?:es)?|going|went) live|"
    r"in(?:to)? production|deployed to production|"
    r"production(?:is|iz)\w*|"
    r"adopt(?:s|ed)"
    r")\b",
    re.I,
)
# "Production" as a bare adjective. It makes the same claim as "in production"
# when it describes the thing that was built: a single-day hackathon build called
# "a guardrailed production interface over civic data" passed every guard and
# landed as the one blocking issue on an otherwise clean review.
#
# It is separate from the pattern above because it is only reliable when the text
# is known to be ABOUT the evidence being checked. A one-line summary is judged
# against every fact in the vault, and "backed by experience automating tests for
# a production rideshare pricing engine" describes the CLIENT's live system,
# truthfully, while some unrelated fact elsewhere is provisional. Folding this
# into the main pattern would have started rejecting summaries like that one.
# See `upgrades_status`.
_ADJECTIVAL_PRODUCTION_RE = re.compile(r"\bproduction\b", re.I)
# Wording that says it did not. A fact carrying one of these has a status, and a
# rewrite is not allowed to quietly promote it: the verified EPAM AI agent was
# "demoed end-to-end; pending senior approval at the time I left", which a
# summary turned into "has shipped an AI agent".
_PROVISIONAL_RE = re.compile(
    r"\b(?:pending|awaiting|not yet|unapproved|prototype|proof of concept|"
    r"poc|demoed|demo(?:ed|ing)?|hackathon|trial|mock|sandbox|"
    r"in progress|work in progress|experimental|draft)\w*\b",
    re.I,
)


def records_provisional_status(text: str) -> bool:
    """True when the evidence itself says the work is not finished.

    Exported so the tailor can warn the writer BEFORE it composes rather than
    only refusing the summary afterwards. A refused summary costs the page its
    opening line and, on every measured baseline run, three points a pass until
    the model happened to stop making the claim.
    """
    return bool(_PROVISIONAL_RE.search(text))


def upgrades_status(
    text: str, source_text: str, *, text_is_about_source: bool = True
) -> bool:
    """True when a rewrite claims completion the evidence does not support.

    Not a hallucination in the metric-and-technology sense, which is why the
    number and technology guards let it through. It is still the resume saying
    something the fact does not, and it is the kind of overstatement an
    interviewer catches.

    `text_is_about_source` says whether `text` is known to describe this
    particular evidence. A bullet rewrite is: it was produced from that fact and
    from nothing else, so a bare "production" in it is a claim about that work.
    A one-line summary is not: it is checked against every fact in the vault in
    turn, so an adjective that could belong to any of them is too weak to reject
    on, and treating it as proof rejected an honest summary about a client's
    genuinely live pricing engine. The explicit wordings, shipped and launched
    and in production, count either way.
    """
    completion = _COMPLETION_RE.search(text) or (
        text_is_about_source and _ADJECTIVAL_PRODUCTION_RE.search(text)
    )
    if not completion:
        return False
    if _COMPLETION_RE.search(source_text) or (
        text_is_about_source and _ADJECTIVAL_PRODUCTION_RE.search(source_text)
    ):
        # The evidence already claims it shipped, so the rewrite is not the one
        # making the claim.
        return False
    return bool(_PROVISIONAL_RE.search(source_text))


# Evidence that explicitly credits the work to a TEAM rather than to the
# candidate. Deliberately narrower than "any ownership hedge": "worked on" hedges
# scope, not authorship, and a rewrite of "worked on and extended the Go test
# suite" into "wrote and maintained automated tests" is an improvement the review
# has never objected to. Naming a team is a different, checkable claim.
_TEAM_CREDIT_RE = re.compile(
    r"\b(?:"
    r"(?:part of|member of|on|with|in|joined) an? team|"
    r"team[- ](?:built|of|based)|"
    r"a team building|"
    r"collaborat\w*|alongside|together with|jointly"
    r")\b",
    re.I,
)
# Any wording in the rewrite that still tells the reader it was not solo work.
# Wider than the literal word "team" on purpose. A real edit produced "Helped
# build an AI agent that generates test cases", which is honest shared credit and
# better writing than the verified original, and a guard that only looked for
# "team" would have reverted it and thrown the improvement away. Anything here
# leaves the reader knowing the candidate did not do it alone, which is the whole
# claim the guard protects.
_TEAM_RETAINED_RE = re.compile(
    r"\b(?:"
    r"team|teams|"
    r"collaborat\w*|alongside|jointly|"
    r"co[- ](?:built|wrote|designed|developed|authored)|"
    r"help(?:ed|ing)?|contribut\w*|assist\w*|supported|"
    r"part of|with others|shared"
    r")\b",
    re.I,
)


def drops_team_credit(text: str, source_text: str) -> bool:
    """True when a rewrite quietly takes sole credit for work a team did.

    Same shape as `upgrades_status`, for the other half of the ownership rule.
    The evidence for the EPAM AI agent reads "was part of a team building an AI
    agent"; a rewrite came back as "Built agentic workflows that generate test
    cases", which invents no metric and no technology and drops the team
    outright. The independent review caught it as an ownership warning on a real
    run, and the career-ops rules are explicit that accuracy beats a
    stronger-sounding verb.

    A rewrite may absolutely replace the weak opener "was part of a team
    building" with a real verb, which the writing rules ask for. It may not
    delete the team while doing it: "built, with a team, an AI agent" satisfies
    both rules at once.
    """
    if not _TEAM_CREDIT_RE.search(source_text):
        return False
    return not _TEAM_RETAINED_RE.search(text)


# A dash with a digit on each side: a numeric range, which keeps its hyphen.
_NUMERIC_RANGE_DASH_RE = re.compile(r"(\d)\s*[\u2013\u2014]\s*(\d)")


def normalize_dashes(text: str | None, *, separator: str = ", ") -> str | None:
    """Replace em dashes, en dashes and double hyphens with real punctuation.

    The user's rule is global and the source facts do not honour it, so the fix
    belongs at assembly: a dash that arrived inside a verified fact must not
    reach the page. Hyphens inside words are left alone, so "cents-per-asset"
    and "0-100" survive untouched.
    """
    if not text:
        return text
    # A dash between two numbers is a range, not punctuation. "0-100" was named
    # as safe in the docstring above, but the example there is a HYPHEN and his
    # fact carries an EN DASH, so a real bullet reading "a 0-100 dig-readiness
    # score" reached the page as "a 0, 100 dig-readiness score". A visible error
    # in a number, produced by the rule that exists to tidy punctuation.
    cleaned = _NUMERIC_RANGE_DASH_RE.sub(r"\1-\2", text)
    cleaned = _DASH_RE.sub(separator, cleaned)
    # A dash immediately before punctuation leaves ", ." behind, which is worse
    # than the dash was.
    cleaned = re.sub(r",\s*([,.;:!?])", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def mentions_word(haystack: str, term: str) -> bool:
    """Whether `haystack` names `term` as a word, not merely as a substring.

    Word boundaries are hand-rolled rather than \\b because the terms this is used
    on include C++, CI/CD and .NET, where the edge character is not a word
    character and \\b lands in the wrong place.
    """
    cleaned = term.strip().casefold()
    if not cleaned:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", haystack.casefold()))


# Subject-matter domains a summary can claim, and every wording on the page that
# would count as evidence for one. Deliberately a short, curated list: a word that
# is not here can never trip the check, which is what keeps false positives bounded.
# The evidence sets are deliberately WIDE, so a bullet that supports the domain in
# different words still counts. "Claims" is evidenced by a bullet saying insurance,
# and "geospatial" by one saying GIS or sewer segments.
DOMAIN_EVIDENCE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # Bare "claim" and "claims" are deliberately NOT evidence. They are polysemous:
    # a real resume evidenced insurance-claims experience with "atomic MongoDB worker
    # claims", which is a concurrency primitive and nothing to do with insurance, and
    # the guard let the overclaim through. Evidence has to be a word that only the
    # domain uses.
    "claims": (
        ("claims", "claim data", "claims data", "claims processing"),
        ("insurance", "insurer", "underwriting", "policyholder", "adjuster",
         "reimbursement", "filed claim", "claim form", "claims processing"),
    ),
    "insurance": (
        ("insurance", "insurtech"),
        ("insurance", "insurer", "underwriting", "policyholder", "premium",
         "filed claim"),
    ),
    "healthcare": (
        ("healthcare", "health care", "clinical", "medical", "patient", "patients"),
        ("health", "healthcare", "clinical", "medical", "patient", "hospital",
         "diagnosis", "diagnostic", "cry", "infant", "hipaa", "ehr"),
    ),
    # "trade" is excluded for the same reason: "cost and latency trade-offs" is
    # ordinary engineering prose and would evidence a trading-desk background.
    "trading": (
        ("trading", "capital markets", "securities", "market making"),
        ("trading", "securities", "equities", "portfolio", "order book",
         "market data", "brokerage"),
    ),
    "geospatial": (
        ("geospatial", "geographic", "mapping data"),
        ("geospatial", "gis", "geojson", "spatial", "map", "maps", "maplibre",
         "sewer", "street", "segments", "basins", "coordinates", "latitude"),
    ),
    "legal": (
        ("legal", "law", "antitrust", "litigation", "compliance data"),
        ("legal", "law", "antitrust", "litigation", "contract", "regulatory",
         "compliance"),
    ),
    "agriculture": (
        ("agriculture", "agricultural", "agritech", "farming"),
        ("agriculture", "agricultural", "farm", "farmer", "crop", "crops",
         "harvest", "soil"),
    ),
    "hiring": (
        ("hiring", "recruiting", "recruitment", "talent"),
        ("hiring", "recruit", "recruiting", "job", "jobs", "posting", "postings",
         "resume", "candidate", "applicant", "ats"),
    ),
    "cybersecurity": (
        ("cybersecurity", "security research", "threat detection"),
        ("security", "vulnerability", "threat", "malware", "intrusion",
         "penetration", "encryption", "tls", "authentication"),
    ),
    "logistics": (
        ("logistics", "supply chain", "fulfilment", "fulfillment"),
        ("logistics", "supply chain", "warehouse", "shipment", "freight",
         "inventory", "delivery", "routing"),
    ),
    "ecommerce": (
        ("ecommerce", "e-commerce", "retail"),
        ("ecommerce", "e-commerce", "retail", "checkout", "cart", "storefront",
         "payments", "orders"),
    ),
}


def unevidenced_domains(summary: str, evidence_text: str) -> list[str]:
    """Subject-matter domains the summary claims that nothing else on the page shows.

    A real tailored summary read "processes real-world geospatial and claims data"
    while no claims project was selected. It invented no number, no technology and
    no completion verb, so every existing guard passed it, yet it claimed a whole
    domain of experience the resume could not back. The independent review caught it
    on one run and missed it on another, which is exactly the kind of check that
    belongs in a rule instead of a model.

    `evidence_text` must exclude the summary itself, or the claim proves itself.
    """
    if not summary.strip():
        return []
    found: list[str] = []
    for domain, (triggers, evidence) in DOMAIN_EVIDENCE.items():
        if not any(mentions_word(summary, trigger) for trigger in triggers):
            continue
        if any(mentions_word(evidence_text, term) for term in evidence):
            continue
        found.append(domain)
    return found


def has_banned_separator(text: str) -> bool:
    """True when text carries a separator the rules keep off the page.

    The tailor normalises these at assembly, but a conversational edit writes
    straight into the document, so the review needs the same test rather than its
    own narrower one.
    """
    return bool(_DASH_RE.search(text))


def content_tokens(text: str) -> frozenset[str]:
    """The meaning-bearing words of a bullet, for similarity comparison.

    Trailing punctuation is stripped per word, so "Cucumber." and "Cucumber,"
    are the same token. Without that, the same sentence ending in a period and
    in a semicolon compared as different vocabulary and duplicate bullets slid
    past the check. Inner dots survive, so "node.js" and "3.0" stay intact.
    """
    words = (word.strip(".") for word in _WORD_RE.findall(text.casefold()))
    return frozenset(w for w in words if len(w) > 2 and w not in _STOPWORDS)


DUPLICATE_THRESHOLD = 0.6
# Containment alone is not enough to call two bullets the same. "Built a payments
# API" and "Built a billing API" share two of three content words, which scores
# 0.67 while describing different systems. Requiring a floor of shared words as
# well means only bullets with real overlap can collapse, and every duplicate
# pair this profile actually contains shares thirteen or more.
MIN_SHARED_TOKENS = 6


def similarity(left: str, right: str) -> float:
    """How much of the shorter bullet the longer one already contains.

    Containment rather than plain Jaccard, because the duplicate pairs that
    actually show up are a terse wording of a job and a fuller wording of the
    same job. Jaccard scores those apart; containment catches them.
    """
    a, b = content_tokens(left), content_tokens(right)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if shared < MIN_SHARED_TOKENS:
        return 0.0
    return shared / min(len(a), len(b))


def dedupe_bullets(bullets: Iterable[str], *, threshold: float = DUPLICATE_THRESHOLD) -> list[str]:
    """Keep one wording per distinct accomplishment, preferring the richer one.

    Re-importing a resume mints a second fact for the same job, so the merged
    entry carries two wordings of every bullet. Both are true; printing both
    makes the resume read like an unedited draft.
    """
    kept: list[str] = []
    for bullet in bullets:
        text = (bullet or "").strip()
        if not text:
            continue
        replaced = False
        for index, existing in enumerate(kept):
            if similarity(existing, text) < threshold:
                continue
            # Same accomplishment. Keep whichever wording carries more
            # substance, measured by concrete words rather than length alone.
            if len(content_tokens(text)) > len(content_tokens(existing)):
                kept[index] = text
            replaced = True
            break
        if not replaced:
            kept.append(text)
    return kept


def _opening_word(text: str) -> str:
    match = re.search(r"[A-Za-z][A-Za-z'-]*", text)
    return match.group(0).casefold() if match else ""


# Where a bullet can be cut in two without anybody having to write a word.
#
# A full stop or a semicolon inside a resume bullet is the author saying "these
# are two statements" in punctuation, so honouring it costs nothing and invents
# nothing. Everything else is left alone on purpose: splitting at a comma
# produces "cutting build time by half" standing on its own, which is a fragment
# rather than a bullet, and a splitter that has to add a verb to fix that is a
# splitter that is writing.
_HARD_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")

# Both halves have to be worth a line of their own. Below this a "split" is a
# bullet with an orphan clause hanging under it, which reads worse than the long
# bullet did.
MIN_SPLIT_WORDS = 6


def split_long_bullet(text: str, *, limit: int = BULLET_MAX_WORDS) -> list[str]:
    """An over-length bullet as the separate statements it already contains.

    A verified bullet the candidate saved at 36 words prints as three lines and
    buries whatever the reader was meant to take from it. The engine used to
    only report that (`too_long_verbatim`), which is honest and leaves the page
    exactly as bad as it was.

    So the page splits what it can split safely. Only at punctuation the author
    already wrote, never at a conjunction, and the words are never touched: the
    output is the input, cut. A bullet with no such boundary comes back
    unchanged and is raised with the user as a gap instead, because shortening
    that one means deciding which of their claims to drop and that is theirs to
    decide.
    """
    body = " ".join(str(text or "").split())
    if not body or len(body.split()) <= limit:
        return [body] if body else []
    pieces = [piece.strip() for piece in _HARD_SPLIT_RE.split(body)]
    pieces = [piece for piece in pieces if piece]
    if len(pieces) < 2 or any(len(piece.split()) < MIN_SPLIT_WORDS for piece in pieces):
        return [body]
    out: list[str] = []
    for piece in pieces:
        # A semicolon ended a clause, not a sentence, so the fragment it leaves
        # behind gets the terminator the rest of the page uses. Nothing is added
        # that carries meaning.
        cleaned = piece.rstrip(";").strip()
        if not cleaned:
            continue
        out.append(cleaned[0].upper() + cleaned[1:])
    return out or [body]


def over_length_bullets(document: dict[str, Any], *, limit: int = BULLET_MAX_WORDS) -> list[str]:
    """Printed bullets still over the cap after splitting, longest first.

    The list the user is asked to fix, so it names the bullet rather than
    counting them: "one bullet is too long" is not something anybody can act on
    without being told which.
    """
    found: list[tuple[int, str]] = []
    for section in ("work", "projects", "volunteer"):
        for entry in document.get(section) or []:
            for highlight in (entry.get("highlights") or []):
                words = len(str(highlight or "").split())
                if words > limit:
                    found.append((words, str(highlight)))
    return [text for _words, text in sorted(found, key=lambda item: -item[0])]


# A bullet the writer printed exactly as the vault holds it is his wording, not
# the model's. That distinction is the whole point of the two checks below: a
# 46-word bullet is a defect either way, but "the writer padded this" and "your
# saved fact is 46 words" have different owners and different fixes, and until
# now they were reported in identical words.
_WHITESPACE_RE = re.compile(r"\s+")


def _comparable(text: str) -> str:
    """Bullet text reduced to what survives assembly, for identity checks."""
    return _WHITESPACE_RE.sub(" ", str(text or "")).strip().casefold()


def is_verbatim_source(text: str, verified_sources: Iterable[str]) -> bool:
    """Did this bullet reach the page as the verified fact, unedited?

    Exact match after whitespace and case, never fuzzy. A near-match is a
    rewrite, and a rewrite that came back over the cap is the writer's to answer
    for. Only an untouched bullet gets to point at the vault.
    """
    target = _comparable(text)
    if not target:
        return False
    return any(_comparable(source) == target for source in verified_sources)


def bullet_flags(text: str, *, source_text: str | None = None) -> list[str]:
    """Writing problems in one bullet, named so a model can fix them.

    `source_text` is the verified wording this bullet was rewritten from. When
    given, growth and padding are judged against it, which is how JD stuffing
    gets caught: the tell is not the phrase itself but the phrase appearing in
    the rewrite and nowhere in the evidence.
    """
    flags: list[str] = []
    words = len(text.split())
    if words > BULLET_MAX_WORDS:
        flags.append(f"too_long({words}w)")
    if _FIRST_PERSON_RE.search(text):
        flags.append("first_person")
    if _WEAK_OPENER_RE.match(text.strip()):
        flags.append("weak_opener")
    lowered = text.casefold()
    banned = {phrase for phrase in BANNED_WORDING if phrase in lowered}
    if _END_TO_END_RE.search(text):
        banned.add("end-to-end")
    banned = sorted(banned)
    if banned:
        flags.append(f"banned_wording({','.join(banned)})")
    padding = {match.group(0).casefold() for match in _JD_PADDING_RE.finditer(text)}
    if source_text is not None:
        source_padding = {
            match.group(0).casefold() for match in _JD_PADDING_RE.finditer(source_text)
        }
        padding -= source_padding
        source_words = len(source_text.split())
        # Growth only matters once the result is long enough for the growth to
        # have cost the reader something. Turning a six-word bullet into twelve
        # words is usually the rewrite doing its job.
        if (
            source_words
            and words > INFLATION_FLOOR_WORDS
            and words > source_words * REWRITE_GROWTH_LIMIT
        ):
            flags.append(f"inflated_rewrite({source_words}w->{words}w)")
    if padding:
        flags.append(f"jd_padding({','.join(sorted(padding))})")
    if _DASH_RE.search(text):
        flags.append("dash")
    return flags


def _attributed_bullet_flags(
    text: str, *, verified_sources: Iterable[str] = ()
) -> list[str]:
    """`bullet_flags`, with length blamed on whoever actually chose it.

    Only `too_long` is re-attributed. Everything else it can report is something
    the writer did to the text, so a verbatim bullet cannot carry it: padding,
    first person and invented metrics are all absent from a verified fact by the
    time it is in the vault.
    """
    flags = bullet_flags(text)
    if not is_verbatim_source(text, verified_sources):
        return flags
    return [
        f"too_long_verbatim({flag[len('too_long('):]}" if flag.startswith("too_long(") else flag
        for flag in flags
    ]


def _phrases(text: str, length: int) -> set[str]:
    words = _WORD_RE.findall(text.casefold())
    return {
        " ".join(words[i : i + length]) for i in range(max(0, len(words) - length + 1))
    }


# Long enough that two bullets sharing one is repetition rather than shared
# vocabulary. Five words catches "adding regression coverage as pricing rules
# shipped" appearing verbatim on two different bullets in the same role.
REPEATED_PHRASE_WORDS = 5

# `section_flags` runs per entry, so it can see two bullets inside one project
# that both open "Built" and structurally cannot see five projects that each
# open "Built". On a resume whose every entry is a thing the candidate made,
# that second case is the one a reader actually notices, and it had never fired.
#
# Across a whole page one repeated verb is normal English, so this is a share
# rather than a count: an opener has to carry more than a third of the page's
# bullets, and at least this many, before the page reads as one sentence
# rewritten.
PAGE_OPENER_SHARE = 1 / 3
MIN_PAGE_OPENER_REPEATS = 3


def section_flags(
    bullets: list[str], *, verified_sources: Iterable[str] = ()
) -> list[str]:
    """Problems that only exist between bullets, not inside one."""
    sources = list(verified_sources)
    flags: list[str] = []
    for index, bullet in enumerate(bullets):
        for other in bullets[index + 1 :]:
            if similarity(bullet, other) >= DUPLICATE_THRESHOLD:
                flags.append("near_duplicate_bullets")
                break
        if flags:
            break
    printed = [b for b in bullets if b.strip()]
    openers = [_opening_word(b) for b in printed]
    repeated = {opener for opener in openers if opener and openers.count(opener) > 1}
    # An opener only repeats because of the wordings that carry it. When every
    # one of those reached the page untouched, the repetition is in the vault and
    # no pass of the writer can honestly remove it: the alternative to his
    # wording is not a better verb, it is a different claim.
    inherited = {
        opener
        for opener in repeated
        if all(
            is_verbatim_source(bullet, sources)
            for bullet in printed
            if _opening_word(bullet) == opener
        )
    }
    authored = repeated - inherited
    if authored:
        flags.append(f"repeated_opening_verb({','.join(sorted(authored))})")
    if inherited:
        flags.append(f"repeated_opening_verb_verbatim({','.join(sorted(inherited))})")
    # Two distinct bullets that end the same way read as machine-written even
    # when neither is a duplicate of the other. The review caught a role whose
    # second and third bullets both closed "adding regression coverage as pricing
    # rules shipped".
    shared: set[str] = set()
    for index, bullet in enumerate(bullets):
        for other in bullets[index + 1 :]:
            shared |= _phrases(bullet, REPEATED_PHRASE_WORDS) & _phrases(
                other, REPEATED_PHRASE_WORDS
            )
    if shared:
        flags.append(f"repeated_phrase({sorted(shared)[0]})")
    return flags


def page_opener_flags(
    document: dict[str, Any], *, verified_sources: Iterable[str] = ()
) -> list[str]:
    """One verb opening most of the page, which no single entry can see."""
    sources = list(verified_sources)
    printed = [
        highlight
        for section in ("work", "projects", "volunteer")
        for entry in (document.get(section) or [])
        for highlight in (entry.get("highlights") or [])
        if str(highlight or "").strip()
    ]
    if len(printed) < MIN_PAGE_OPENER_REPEATS:
        return []
    openers = [_opening_word(bullet) for bullet in printed]
    flags: list[str] = []
    for opener in sorted(set(openers)):
        if not opener:
            continue
        count = openers.count(opener)
        if count < MIN_PAGE_OPENER_REPEATS:
            continue
        if count <= len(printed) * PAGE_OPENER_SHARE:
            continue
        carriers = [b for b in printed if _opening_word(b) == opener]
        inherited = all(is_verbatim_source(b, sources) for b in carriers)
        name = "page_opener_verbatim" if inherited else "page_opener"
        flags.append(f"{name}({opener} opens {count} of {len(printed)})")
    return flags


# --------------------------------------------------------------------------
# Reader-side checks.
#
# The rules above are about how a bullet is written. These are about whether the
# page answers the questions its three readers actually ask: a recruiter
# checking basic eligibility, a sourcer deciding which team you point at, and a
# hiring manager looking for something to ask you about. They come from a
# resume session run by NVIDIA recruiters and engineers (August 2026), where the
# most common rejections were not bad prose but missing facts.
# --------------------------------------------------------------------------

# A graduation date that is a bare year fails the check the recruiter actually
# runs, which is whether you are available for a given cycle. "2028" does not
# distinguish a May graduate from a December one. Session notes list a missing
# or year-only graduation date as the single most common defect.
_ISO_YEAR_MONTH_RE = re.compile(r"^\s*\d{4}-(0[1-9]|1[0-2])")
_MONTH_NAME_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)", re.I
)


def has_month_and_year(value: object) -> bool:
    """Does this date string name a month as well as a year?"""
    text = str(value or "").strip()
    if not text:
        return False
    if not re.search(r"\b(?:19|20)\d{2}\b", text):
        return False
    return bool(_ISO_YEAR_MONTH_RE.match(text) or _MONTH_NAME_RE.search(text))


def education_flags(document: dict) -> list[str]:
    """Whether the education block answers a recruiter's first question."""
    entries = document.get("education") or []
    if not entries:
        return ["missing_education"]
    flags: list[str] = []
    if not any(has_month_and_year(entry.get("endDate")) for entry in entries):
        flags.append("no_graduation_month_and_year")
    return flags


# Links get clicked. A resume naming a GitHub project without a URL anywhere on
# the page asks the reader to go and search for it, which they will not do.
_LINK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github", re.compile(r"github\.com/\w", re.I)),
    ("linkedin", re.compile(r"linkedin\.com/", re.I)),
)


def missing_link_kinds(document: dict) -> list[str]:
    """Link types the page never carries, in basics or on a project."""
    basics = document.get("basics") or {}
    haystack = [str(basics.get("url") or "")]
    haystack += [
        str((profile or {}).get("url") or "") for profile in (basics.get("profiles") or [])
    ]
    haystack += [
        str((project or {}).get("url") or "") for project in (document.get("projects") or [])
    ]
    blob = " ".join(haystack)
    return [name for name, pattern in _LINK_PATTERNS if not pattern.search(blob)]


def unlinked_projects(document: dict[str, Any]) -> list[str]:
    """Projects printed on the page with no URL on their heading.

    Every template hyperlinks the project name and prints nothing when there is
    no link, so a missing URL is silent: the reader is asked to go and search
    for the repository, which they will not do. Naming the project makes it one
    profile edit rather than a mystery.
    """
    return [
        str(project.get("name") or "").strip()
        for project in (document.get("projects") or [])
        if isinstance(project, dict)
        and str(project.get("name") or "").strip()
        and not str(project.get("url") or "").strip()
    ]


# A skill row is a claim. The session's phrasing for the failure is
# "technologies listed without showing how they were used": the reader cannot
# tell a language you shipped in from one you read a tutorial about, so the
# skill has to appear inside a bullet somewhere, doing something.
def unevidenced_skills(
    document: dict[str, Any], *, vault_evidence: Iterable[str] = ()
) -> list[str]:
    """Skills claimed on the page that nothing behind it can back.

    The rule used to be "no bullet on this page demonstrates it", which is the
    right question to ask of a resume nobody tailored: an uploaded page is all
    the evidence there is. It is the wrong question to ask of a tailored one.

    A one-page resume prints three projects and a dozen bullets out of a whole
    career. A truthful, verified skills list will always name things those
    twelve bullets had no room to show, and this flagged every one: thirty-four
    on a real run, each costing points, so the more complete the vault the worse
    the page scored. That is a rule pointing away from the goal.

    Given `vault_evidence`, the question becomes the one worth asking: is there
    anything at all behind this claim? A skill the candidate verified, or that
    any of his bullets describe, is backed whether or not this particular page
    had room for it. A keyword that appears nowhere in the vault is a claim with
    nothing behind it, which is the interview-collapsing case the check was
    written for, and it is still flagged.
    """
    page: list[str] = []
    for section in ("work", "projects", "volunteer"):
        for entry in document.get(section) or []:
            page.extend(str(h) for h in (entry.get("highlights") or []) if h)
            for key in ("name", "position", "description", "summary"):
                if entry.get(key):
                    page.append(str(entry[key]))
    evidence = [*page, *vault_evidence]
    blob = " ".join(evidence)
    if not blob.strip():
        return []
    missing: list[str] = []
    for group in document.get("skills") or []:
        for keyword in (group or {}).get("keywords") or []:
            term = str(keyword).strip()
            if term and not mentions_word(blob, term):
                missing.append(term)
    return sorted(set(missing))


# Quantification is a soft signal on purpose. Requiring a number per bullet
# would collide head-on with the no-fabrication rule and push the model to
# invent one, which is the worse failure: a made-up metric is the thing that
# collapses in the interview the resume was supposed to win. So this only
# notices a page with no numbers anywhere, where the fix is to surface a real
# figure that already exists in the vault.
_NUMBER_RE = re.compile(r"\d")


def quantified_bullets(document: dict) -> tuple[int, int]:
    """(bullets carrying a number, total bullets)."""
    total = 0
    numeric = 0
    for section in ("work", "projects", "volunteer"):
        for entry in document.get(section) or []:
            for highlight in (entry.get("highlights") or []):
                if not highlight:
                    continue
                total += 1
                if _NUMBER_RE.search(str(highlight)):
                    numeric += 1
    return numeric, total


def _evidence_text(document: dict) -> str:
    """Everything the resume says apart from its summary line.

    The summary is what is being judged, so including it would let any claim prove
    itself.
    """
    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for key, value in document.items():
        if key == "basics":
            basics = {k: v for k, v in (value or {}).items() if k != "summary"}
            walk(basics)
            continue
        walk(value)
    return " ".join(parts).casefold()


def document_quality_flags(
    document: dict[str, Any],
    *,
    verified_sources: Iterable[str] = (),
    vault_evidence: Iterable[str] = (),
    template_key: str | None = None,
) -> dict[str, list[str]]:
    """Every writing problem in an assembled resume, keyed by where it lives.

    `verified_sources` is the vault wording behind the page, when the caller has
    it. Given, length and opening-verb problems are attributed: a bullet printed
    exactly as the fact holds it reports as `_verbatim`, which the tailor does
    not charge the writer for, because the writer chose the safest thing
    available and the defect it inherited is the user's to edit. Omitted, every
    bullet is treated as authored, which is right for the review of a resume
    nobody tailored.

    `vault_evidence` is everything the profile holds, for the skills check. See
    `unevidenced_skills`: without it the question is "does this page show it",
    which is the only question an uploaded resume can answer and the wrong one
    to ask of a tailored page that prints twelve bullets out of a career.
    """
    sources = list(verified_sources)
    evidence = list(vault_evidence)
    found: dict[str, list[str]] = {}
    summary = str((document.get("basics") or {}).get("summary") or "").strip()
    if summary:
        # The summary is where JD parroting is most tempting, because a
        # requirement can be restated there without touching a single bullet. A
        # real run opened with the posting's own "a strong grasp of data
        # structures, algorithms, and systems".
        summary_flags = [
            flag for flag in bullet_flags(summary) if not flag.startswith("weak_opener")
        ]
        words = len(summary.split())
        if words > SUMMARY_MAX_WORDS:
            summary_flags.append(f"summary_too_long({words}w)")
        summary_flags = [f for f in summary_flags if not f.startswith("too_long")]
        # Judged against the rest of the page, never against itself.
        overclaimed = unevidenced_domains(summary, _evidence_text(document))
        if overclaimed:
            summary_flags.append(f"unevidenced_domain({','.join(overclaimed)})")
        if summary_flags:
            found["basics.summary"] = sorted(set(summary_flags))
    for section, label_keys in (
        ("work", ("position", "name")),
        ("projects", ("name",)),
        ("volunteer", ("position", "organization")),
    ):
        for entry in document.get(section) or []:
            label = next(
                (str(entry.get(key)) for key in label_keys if entry.get(key)),
                section,
            )
            highlights = [h for h in (entry.get("highlights") or []) if h]
            entry_flags: list[str] = list(
                section_flags(highlights, verified_sources=sources)
            )
            for highlight in highlights:
                entry_flags.extend(
                    _attributed_bullet_flags(highlight, verified_sources=sources)
                )
            if len(highlights) > (
                MAX_WORK_BULLETS if section == "work" else MAX_PROJECT_BULLETS
            ):
                entry_flags.append(f"too_many_bullets({len(highlights)})")
            if entry_flags:
                found[f"{section}: {label}"] = sorted(set(entry_flags))
    groups = document.get("skills") or []
    skill_flags: list[str] = []
    if len(groups) > MAX_SKILL_GROUPS:
        skill_flags.append(f"too_many_groups({len(groups)})")
    unevidenced = unevidenced_skills(document, vault_evidence=evidence)
    if unevidenced:
        # Capped: a page listing fifteen unevidenced skills has one problem, not
        # fifteen, and naming them all buries the rest of the report.
        shown = ",".join(unevidenced[:6])
        extra = f",+{len(unevidenced) - 6}" if len(unevidenced) > 6 else ""
        skill_flags.append(f"unevidenced_skill({shown}{extra})")
    if skill_flags:
        found["skills"] = skill_flags

    education = education_flags(document)
    if education:
        found["education"] = education

    missing_links = missing_link_kinds(document)
    if missing_links:
        found["links"] = [f"no_{kind}_link" for kind in missing_links]

    numeric, total = quantified_bullets(document)
    if total and numeric == 0:
        found["impact"] = ["no_quantified_bullets"]
    rendered_bullets = printed_bullets(document)
    page: list[str] = []
    if rendered_bullets < MIN_PAGE_BULLETS:
        page.append(f"thin_page({rendered_bullets} bullets)")
    else:
        # Against the template's own budget, not the generic one. Flagging
        # husky at 47 reports a page as fine at a length that renders two.
        budget = page_shape(template_key).max_lines
        lines = estimated_page_lines(document, template_key)
        if lines > budget:
            page.append(f"over_page({lines} of {budget} lines)")
    page.extend(page_opener_flags(document, verified_sources=sources))
    if page:
        found["page"] = page
    return found


# A section that renders at all costs its heading plus its rule.
_SECTION_HEADING_LINES = 2
# Skills print as "Category: a, b, c" and wrap like any other prose.
_SKILL_WORDS_PER_LINE = 10


def _wrapped(text: str, words_per_line: int = WORDS_PER_RENDERED_LINE) -> int:
    words = len(str(text or "").split())
    return max(1, math.ceil(words / words_per_line)) if words else 0


def printed_bullets(document: dict[str, Any]) -> int:
    """Bullets the page actually shows, across roles, projects and volunteering.

    Named and shared because the page-fit cut has to ask the same question the
    `thin_page` flag asks. It did not: the cut guarded the project count and
    never the bullet count, so a run cut a three-bullet project off a
    nine-bullet page and shipped six, trading `over_page` for `thin_page`.
    """
    return sum(
        len([h for h in (entry.get("highlights") or []) if h])
        for section in ("work", "projects", "volunteer")
        for entry in (document.get(section) or [])
    )


def estimated_page_lines(
    document: dict[str, Any], template_key: str | None = None
) -> int:
    """Rendered lines the whole page will take, close enough to budget by.

    It used to count the roles and projects and nothing else, which is most of
    the words but nowhere near all of the page. Measured against a real tailored
    document: it estimated 28 lines against a budget of 30, and rendered to TWO
    pages on husky, jakes, sb2nov and moderncv alike. Not a template being
    dense, an estimate that was ignoring the summary, the education, the skills
    and the certificates entirely, on a page where those came to roughly fifteen
    more lines.

    That mattered beyond the flag. The page-fit cut added in #45 decides which
    project to drop by comparing this number to the budget, so an estimate that
    undercounts by a third does not cut when it should, and the resume spills
    with every project still on it.

    Still not exact and still does not need to be. It has to tell a page that
    fits from a page that spills.
    """
    lines = 0

    # The lede. Prose at the top of the page, and on a real resume it is three
    # lines, not zero.
    # Only where the template has somewhere to put it. Counting a summary that
    # husky will not draw inflates the estimate and then invites the trimmer to
    # delete the summary to recover lines the page never spent.
    if page_shape(template_key).renders_summary:
        summary = (document.get("basics") or {}).get("summary")
        lines += _wrapped(summary)

    for section in ("work", "projects", "volunteer"):
        entries = document.get(section) or []
        rendered = False
        for entry in entries:
            highlights = [h for h in (entry.get("highlights") or []) if h]
            if not highlights and section != "work":
                continue
            rendered = True
            lines += 1
            for highlight in highlights:
                lines += _wrapped(highlight)
        if rendered:
            lines += _SECTION_HEADING_LINES

    # One row per entry for the list sections, plus their heading. A degree, a
    # certificate and an award each print as a line naming it and its issuer.
    for section in ("education", "certificates", "awards", "publications"):
        entries = [e for e in (document.get(section) or []) if e]
        if entries:
            lines += _SECTION_HEADING_LINES + len(entries)

    # Skills wrap by keyword count, not by group count: six groups holding
    # forty-three keywords is not six lines.
    groups = [g for g in (document.get("skills") or []) if g]
    if groups:
        lines += _SECTION_HEADING_LINES
        for group in groups:
            keywords = [k for k in (group.get("keywords") or []) if k]
            label = str(group.get("name") or "")
            lines += _wrapped(
                " ".join([label, *(str(k) for k in keywords)]), _SKILL_WORDS_PER_LINE
            )
    return lines
