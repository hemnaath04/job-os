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
# outright. Counting bullets cannot see it coming: 11 bullets over 4 entries
# rendered to two pages while 10 over 3 fitted on one, because an entry costs a
# heading row and a long bullet wraps onto a second line. So the budget is
# measured in estimated rendered lines and calibrated against Tectonic on real
# tailored documents: 26 and 29 estimated lines each rendered to one page, 33 to
# two. 30 sits above every measured single-pager with room to spare.
MAX_PAGE_LINES = 30
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
    """a an and as at by for from in into onto of on or over under the to with
    that which while across most new using via per each its their his her them
    it was were are not this these those also than then such same other more
    less many much some any all both own very just only after before during
    directly""".split()
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
    "innovative solution",
    "robust architecture",
    "seamlessly",
    "seamless",
    "end-to-end solution",
    "comprehensive",
    "sophisticated",
    "holistic",
    "synergy",
    "meticulous",
    "pivotal",
)

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


def normalize_dashes(text: str | None, *, separator: str = ", ") -> str | None:
    """Replace em dashes, en dashes and double hyphens with real punctuation.

    The user's rule is global and the source facts do not honour it, so the fix
    belongs at assembly: a dash that arrived inside a verified fact must not
    reach the page. Hyphens inside words are left alone, so "cents-per-asset"
    and "0-100" survive untouched.
    """
    if not text:
        return text
    cleaned = _DASH_RE.sub(separator, text)
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
    banned = sorted(phrase for phrase in BANNED_WORDING if phrase in lowered)
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


def _phrases(text: str, length: int) -> set[str]:
    words = _WORD_RE.findall(text.casefold())
    return {
        " ".join(words[i : i + length]) for i in range(max(0, len(words) - length + 1))
    }


# Long enough that two bullets sharing one is repetition rather than shared
# vocabulary. Five words catches "adding regression coverage as pricing rules
# shipped" appearing verbatim on two different bullets in the same role.
REPEATED_PHRASE_WORDS = 5


def section_flags(bullets: list[str]) -> list[str]:
    """Problems that only exist between bullets, not inside one."""
    flags: list[str] = []
    for index, bullet in enumerate(bullets):
        for other in bullets[index + 1 :]:
            if similarity(bullet, other) >= DUPLICATE_THRESHOLD:
                flags.append("near_duplicate_bullets")
                break
        if flags:
            break
    openers = [_opening_word(b) for b in bullets if b.strip()]
    repeated = {opener for opener in openers if opener and openers.count(opener) > 1}
    if repeated:
        flags.append(f"repeated_opening_verb({','.join(sorted(repeated))})")
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


def document_quality_flags(document: dict) -> dict[str, list[str]]:
    """Every writing problem in an assembled resume, keyed by where it lives."""
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
            entry_flags: list[str] = list(section_flags(highlights))
            for highlight in highlights:
                entry_flags.extend(bullet_flags(highlight))
            if len(highlights) > (
                MAX_WORK_BULLETS if section == "work" else MAX_PROJECT_BULLETS
            ):
                entry_flags.append(f"too_many_bullets({len(highlights)})")
            if entry_flags:
                found[f"{section}: {label}"] = sorted(set(entry_flags))
    groups = document.get("skills") or []
    if len(groups) > MAX_SKILL_GROUPS:
        found["skills"] = [f"too_many_groups({len(groups)})"]
    rendered_bullets = sum(
        len([h for h in (entry.get("highlights") or []) if h])
        for section in ("work", "projects", "volunteer")
        for entry in (document.get(section) or [])
    )
    if rendered_bullets < MIN_PAGE_BULLETS:
        found["page"] = [f"thin_page({rendered_bullets} bullets)"]
    else:
        lines = estimated_page_lines(document)
        if lines > MAX_PAGE_LINES:
            found["page"] = [f"over_page({lines} of {MAX_PAGE_LINES} lines)"]
    return found


def estimated_page_lines(document: dict) -> int:
    """Rendered lines the roles and projects will take, close enough to budget by.

    One row for each entry's heading, then one row per line of bullet text. Not
    exact, and it does not need to be: it only has to tell a page that fits from
    a page that spills, which counting bullets alone could not.
    """
    lines = 0
    for section in ("work", "projects", "volunteer"):
        for entry in document.get(section) or []:
            highlights = [h for h in (entry.get("highlights") or []) if h]
            if not highlights and section != "work":
                continue
            lines += 1
            for highlight in highlights:
                lines += max(
                    1, math.ceil(len(highlight.split()) / WORDS_PER_RENDERED_LINE)
                )
    return lines
