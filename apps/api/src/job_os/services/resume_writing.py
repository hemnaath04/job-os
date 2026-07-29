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

# Wording that says a thing reached users or production.
_COMPLETION_RE = re.compile(
    r"\b(?:shipped|launched|released|delivered|rolled out|went live|"
    r"in production|productionis|productioniz|adopted|"
    r"deployed to production)\w*\b",
    re.I,
)
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


def upgrades_status(text: str, source_text: str) -> bool:
    """True when a rewrite claims completion the evidence does not support.

    Not a hallucination in the metric-and-technology sense, which is why the
    number and technology guards let it through. It is still the resume saying
    something the fact does not, and it is the kind of overstatement an
    interviewer catches.
    """
    if not _COMPLETION_RE.search(text):
        return False
    if _COMPLETION_RE.search(source_text):
        # The evidence already claims it shipped, so the rewrite is not the one
        # making the claim.
        return False
    return bool(_PROVISIONAL_RE.search(source_text))


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
    return found
