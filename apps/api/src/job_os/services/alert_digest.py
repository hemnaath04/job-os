"""Compose one digest email: dedupe, then render text and HTML.

Three promises this module keeps, all of them things the market leader is
complained about for breaking:

1. A job already mailed to a user is never mailed to that user again. The sent
   log is the authority and `build_digest` will not put a row in an email if
   either of its two keys is already in the log. See models/alert.py.
2. An empty digest is never sent. `build_digest` returns None rather than an
   object with no rows, so "send nothing" is not a decision a caller can forget
   to make.
3. Freshness is labelled honestly, including when that means admitting the date
   is a repost or an estimate. See services/alert_freshness.py.

Rendering: two parts, always. The HTML is deliberately old fashioned, nested
tables with inline styles and nothing newer than CSS 2, because Outlook renders
mail through Word and a flexbox layout collapses into a single column of
unstyled text there. Nothing here needs a modern layout engine.

Copy style: no em dashes, sentences that a person would say out loud, and no
claim the data does not support.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from uuid import UUID

from job_os.integrations.email import EmailMessage
from job_os.services.alert_freshness import Freshness, assess_freshness

#: Body width. 600px is the width every mail client has agreed to render since
#: roughly 2005 and is what Outlook's reading pane assumes.
BODY_WIDTH_PX = 600

#: The layout table this email is built out of, opened the same way every time.
#: role="presentation" keeps a screen reader from announcing the layout as a data
#: table, and the zeroed border and spacing attributes are there because Outlook
#: ignores the CSS equivalents.
_TABLE_OPEN = '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">'

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[^a-z0-9 ]+")

#: Deliberately narrow. It has to be sure enough that a match can be shown to a
#: user as "this is what the posting says", so it requires an explicit currency
#: marker and a plausible annual figure, and it does not try to read hourly rates
#: or equity ranges at all. A missing salary is fine; a wrong one is not.
_SALARY_RE = re.compile(
    r"(?P<cur>[$€£]|USD|CAD|EUR|GBP|INR)\s?"
    r"(?P<low>\d{1,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]\b|\d{5,7})"
    r"(?:\s?(?:[-–]|to)\s?(?:[$€£]|USD|CAD|EUR|GBP|INR)?\s?"
    r"(?P<high>\d{1,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]\b|\d{5,7}))?",
    re.IGNORECASE,
)
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
_SALARY_FLOOR = 10_000
_SALARY_CEILING = 2_000_000


def normalize_for_key(value: str | None) -> str:
    """Fold a field down to the part that identifies it.

    Accents stripped, case dropped, punctuation removed, whitespace collapsed. A
    repost that changes "Sr. Engineer (Remote)" to "Senior Engineer, Remote" will
    still slip past this, which is the honest limit of a content hash. The
    source_key catches the same listing; this catches the same text.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = _PUNCTUATION_RE.sub(" ", ascii_only.lower())
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def source_key(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


def content_key(*, company_name: str | None, title: str, location: str | None) -> str:
    """Hash of the normalised role identity, for catching reposts and cross-source duplicates."""
    parts = [
        normalize_for_key(company_name),
        normalize_for_key(title),
        normalize_for_key(location),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateJob:
    """A job that might go into a digest.

    Its own type rather than `DiscoveryResult` so the digest path does not move
    every time the discovery schema does, and so a candidate can also be built
    from a `jobs` row, which carries salary columns that a search result does not.
    """

    source: str
    source_id: str
    source_url: str
    title: str
    company_name: str | None = None
    location: str | None = None
    description: str = ""
    #: What the source claims. Treated as approximate everywhere downstream.
    posted_at: datetime | None = None
    #: The earliest time we have a record of this role. Ours, so it means what it says.
    first_seen_at: datetime | None = None
    source_label: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None

    @classmethod
    def from_discovery_result(cls, result: object) -> CandidateJob:
        return cls(
            source=str(getattr(result, "source", "")),
            source_id=str(getattr(result, "source_id", "")),
            source_url=str(getattr(result, "source_url", "")),
            title=str(getattr(result, "title", "")),
            company_name=getattr(result, "company_name", None),
            location=getattr(result, "location", None),
            description=getattr(result, "description", "") or "",
            posted_at=getattr(result, "posted_at", None),
            source_label=getattr(result, "source_label", None),
        )

    @property
    def source_key(self) -> str:
        return source_key(self.source, self.source_id)

    @property
    def content_key(self) -> str:
        return content_key(
            company_name=self.company_name, title=self.title, location=self.location
        )


@dataclass(frozen=True, slots=True)
class SalaryNote:
    text: str
    #: True when the figures were read out of the posting body rather than given
    #: to us as structured fields. Shown to the reader, because "we parsed this
    #: out of a paragraph" and "the board told us" are not the same claim.
    from_posting_text: bool


@dataclass(frozen=True, slots=True)
class DigestJob:
    title: str
    company: str
    location: str
    url: str
    source_label: str
    freshness: Freshness
    salary: SalaryNote | None
    source: str
    source_id: str
    source_key: str
    content_key: str
    posted_at: datetime | None
    first_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class Digest:
    subscription_id: UUID
    user_id: UUID
    recipient: str
    search_name: str
    cadence: str
    jobs: Sequence[DigestJob]
    generated_at: datetime
    unsubscribe_url: str
    unsubscribe_all_url: str
    postal_address: str
    #: How many candidates the sent log dropped. Reported in the run summary, and
    #: not shown in the email: the reader does not need to know about the ones
    #: they already saw.
    deduped_count: int = 0
    #: Candidates found beyond the per-email cap. Shown as a count with a link
    #: back to the app rather than silently truncated.
    overflow_count: int = 0
    repost_count: int = 0

    @property
    def subject(self) -> str:
        count = len(self.jobs)
        noun = "role" if count == 1 else "roles"
        return f"{count} new {noun} for {self.search_name}"


def _parse_amount(raw: str) -> int | None:
    text = raw.strip().replace(",", "")
    multiplier = 1
    if text[-1:].lower() == "k":
        text = text[:-1].strip()
        multiplier = 1_000
    try:
        value = int(round(float(text) * multiplier))
    except ValueError:
        return None
    return value if _SALARY_FLOOR <= value <= _SALARY_CEILING else None


def _format_range(low: int, high: int | None, currency: str) -> str:
    if high is None or high == low:
        return f"{currency} {low:,}"
    return f"{currency} {low:,} to {high:,}"


def salary_note(candidate: CandidateJob) -> SalaryNote | None:
    """The salary line, or None when we do not have one worth showing.

    Structured fields win. Only if there are none does it look at the posting
    text, and then it says so.
    """
    if candidate.salary_min or candidate.salary_max:
        low = candidate.salary_min or candidate.salary_max
        high = candidate.salary_max if candidate.salary_min else None
        assert low is not None  # guarded by the branch
        currency = (candidate.salary_currency or "USD").upper()
        return SalaryNote(text=_format_range(low, high, currency), from_posting_text=False)

    if not candidate.description:
        return None
    match = _SALARY_RE.search(candidate.description)
    if match is None:
        return None
    low = _parse_amount(match.group("low"))
    if low is None:
        return None
    high_raw = match.group("high")
    high = _parse_amount(high_raw) if high_raw else None
    if high is not None and high < low:
        low, high = high, low
    marker = match.group("cur")
    currency = _CURRENCY_SYMBOLS.get(marker, marker.upper())
    return SalaryNote(text=_format_range(low, high, currency), from_posting_text=True)


def build_digest(
    *,
    subscription_id: UUID,
    user_id: UUID,
    recipient: str,
    search_name: str,
    cadence: str,
    candidates: Iterable[CandidateJob],
    already_sent_source_keys: set[str],
    already_sent_content_keys: set[str],
    known_first_seen: Mapping[str, datetime] | None = None,
    unsubscribe_url: str,
    unsubscribe_all_url: str,
    postal_address: str,
    now: datetime | None = None,
    max_jobs: int = 25,
) -> Digest | None:
    """Assemble a digest, or None when there is nothing new to say.

    `known_first_seen` maps a content_key to the earliest time we have a record
    of that role, which is what makes a repost recognisable. The caller builds it
    from the sent log and the jobs table; a candidate's own `first_seen_at` wins
    when it has one.

    Deduping happens inside the loop, not before it, so a batch that contains the
    same role twice, which happens whenever two sources carry one listing, drops
    the second copy too.
    """
    now = now or datetime.now(UTC)
    known_first_seen = known_first_seen or {}

    rows: list[DigestJob] = []
    seen_source: set[str] = set()
    seen_content: set[str] = set()
    deduped = 0

    for candidate in candidates:
        if not candidate.source_id or not candidate.title:
            # No stable identity means no way to promise we will not send it
            # again. Dropping it is the only option that keeps promise 1.
            deduped += 1
            continue

        s_key = candidate.source_key
        c_key = candidate.content_key
        if (
            s_key in already_sent_source_keys
            or c_key in already_sent_content_keys
            or s_key in seen_source
            or c_key in seen_content
        ):
            deduped += 1
            continue
        seen_source.add(s_key)
        seen_content.add(c_key)

        first_seen = candidate.first_seen_at or known_first_seen.get(c_key)
        freshness = assess_freshness(
            posted_at=candidate.posted_at, first_seen_at=first_seen, now=now
        )
        rows.append(
            DigestJob(
                title=candidate.title.strip(),
                company=(candidate.company_name or "Company not named").strip(),
                location=(candidate.location or "Location not given").strip(),
                url=candidate.source_url,
                source_label=(candidate.source_label or candidate.source).strip(),
                freshness=freshness,
                salary=salary_note(candidate),
                source=candidate.source,
                source_id=candidate.source_id,
                source_key=s_key,
                content_key=c_key,
                posted_at=candidate.posted_at,
                first_seen_at=first_seen,
            )
        )

    if not rows:
        return None

    # Freshest first, by the age the label actually claims, so the order agrees
    # with the words. Unknown ages sort last rather than as brand new.
    rows.sort(key=lambda r: (r.freshness.age_hours is None, r.freshness.age_hours or 0.0))
    overflow = max(len(rows) - max_jobs, 0)
    kept = rows[:max_jobs]

    return Digest(
        subscription_id=subscription_id,
        user_id=user_id,
        recipient=recipient,
        search_name=search_name,
        cadence=cadence,
        jobs=kept,
        generated_at=now,
        unsubscribe_url=unsubscribe_url,
        unsubscribe_all_url=unsubscribe_all_url,
        postal_address=postal_address,
        deduped_count=deduped,
        overflow_count=overflow,
        repost_count=sum(1 for r in kept if r.freshness.is_repost),
    )


# ---- Rendering --------------------------------------------------------------


def _repost_phrase(count: int) -> str:
    """The repost banner sentence, agreeing with itself.

    Both parts of the email say this, so it lives in one place. Pluralising the
    noun and leaving the verb alone produced "1 listing below look like reposts",
    which is the kind of thing a reader notices and quietly downgrades you for.
    """
    if count == 1:
        return "1 listing below looks like a repost."
    return f"{count} listings below look like reposts."


def render_text(digest: Digest) -> str:
    lines: list[str] = [
        digest.subject,
        f"Saved search: {digest.search_name} ({digest.cadence} alert)",
        "",
    ]
    if digest.repost_count:
        lines += [
            f"Note: {_repost_phrase(digest.repost_count)} "
            "We say so on each one rather than showing you the fresh-looking date.",
            "",
        ]

    for index, job in enumerate(digest.jobs, start=1):
        lines.append(f"{index}. {job.title}")
        lines.append(f"   {job.company} | {job.location}")
        if job.salary:
            suffix = " (read from the posting text)" if job.salary.from_posting_text else ""
            lines.append(f"   Pay: {job.salary.text}{suffix}")
        lines.append(f"   {job.freshness.summary}")
        lines.append(f"   {job.url}")
        lines.append(f"   Source: {job.source_label}")
        lines.append("")

    if digest.overflow_count:
        lines += [
            f"{digest.overflow_count} more matched and did not fit in this email. "
            "Open the saved search in job.os to see the rest.",
            "",
        ]

    lines += [
        "How we date these: a job board resets a posting date every time a "
        "recruiter republishes a role, so we treat every date a board gives us as "
        "an estimate and tell you when we first saw the role ourselves.",
        "",
        f"Unsubscribe from this alert: {digest.unsubscribe_url}",
        f"Turn off all job alerts: {digest.unsubscribe_all_url}",
        "Either link works immediately and needs no sign in.",
        "",
        digest.postal_address,
        "",
    ]
    return "\n".join(lines)


def render_html(digest: Digest) -> str:
    """Table layout, inline styles, no CSS newer than 2.1.

    Outlook renders through Word: no flexbox, no grid, no CSS variables, no
    shorthand `background`, no `rem`. Widths in px and colours in hex.
    """
    text_style = "font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;"
    dim_style = "font-family:Arial,Helvetica,sans-serif;color:#5a5a5a;font-size:13px;"

    rows: list[str] = []
    for job in digest.jobs:
        salary_row = ""
        if job.salary:
            suffix = " (read from the posting text)" if job.salary.from_posting_text else ""
            salary_row = (
                f'<tr><td style="{dim_style}padding:2px 0 0 0;">'
                f"Pay: {escape(job.salary.text)}{escape(suffix)}"
                "</td></tr>"
            )

        caveat_row = ""
        if job.freshness.caveat:
            # The repost note is the point of the email, so it gets a visible
            # box rather than grey small print at the bottom of the row.
            background = "#fff6e5" if job.freshness.is_repost else "#f4f4f4"
            border = "#e0a640" if job.freshness.is_repost else "#dddddd"
            caveat_row = (
                '<tr><td style="padding:6px 0 0 0;">'
                f"{_TABLE_OPEN}"
                f'<tr><td style="{dim_style}background-color:{background};'
                f"border-left:3px solid {border};padding:8px 10px;\">"
                f"{escape(job.freshness.caveat)}"
                "</td></tr></table></td></tr>"
            )

        rows.append(
            "".join(
                [
                    '<tr><td style="padding:0 0 22px 0;">',
                    _TABLE_OPEN,
                    f'<tr><td style="{text_style}font-size:17px;'
                    'font-weight:bold;padding:0 0 3px 0;">',
                    f'<a href="{escape(job.url)}" style="color:#1a1a1a;text-decoration:none;">',
                    f"{escape(job.title)}</a></td></tr>",
                    f'<tr><td style="{text_style}font-size:14px;padding:0 0 3px 0;">',
                    f"{escape(job.company)} &nbsp;&middot;&nbsp; {escape(job.location)}",
                    "</td></tr>",
                    salary_row,
                    f'<tr><td style="{dim_style}padding:2px 0 0 0;">',
                    f"{escape(job.freshness.headline)}</td></tr>",
                    caveat_row,
                    '<tr><td style="padding:8px 0 0 0;">',
                    f'<a href="{escape(job.url)}" style="{text_style}font-size:14px;'
                    'font-weight:bold;color:#1a5fd0;text-decoration:underline;">',
                    "View the posting</a>",
                    f'<span style="{dim_style}"> &nbsp; via {escape(job.source_label)}</span>',
                    "</td></tr>",
                    "</table></td></tr>",
                ]
            )
        )

    repost_banner = ""
    if digest.repost_count:
        repost_banner = (
            f'<tr><td style="{dim_style}background-color:#fff6e5;'
            'border:1px solid #e0a640;padding:10px 12px;">'
            f"{escape(_repost_phrase(digest.repost_count))} We say so on each "
            "one rather than showing you the fresh-looking date."
            "</td></tr><tr><td style=\"height:18px;\">&nbsp;</td></tr>"
        )

    overflow_row = ""
    if digest.overflow_count:
        overflow_row = (
            f'<tr><td style="{dim_style}padding:0 0 18px 0;">'
            f"{digest.overflow_count} more matched and did not fit in this email. "
            "Open the saved search in job.os to see the rest.</td></tr>"
        )

    return "".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f"<title>{escape(digest.subject)}</title>",
            "</head>",
            '<body style="margin:0;padding:0;background-color:#f2f2f0;">',
            # Preheader. Hidden in the body, shown in the inbox list, so the
            # preview is the honest promise rather than the first stray words.
            '<div style="display:none;font-size:1px;color:#f2f2f0;line-height:1px;'
            'max-height:0;max-width:0;opacity:0;overflow:hidden;">',
            escape(
                f"{len(digest.jobs)} new for {digest.search_name}. "
                "Dates checked against our own records."
            ),
            "</div>",
            '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" ',
            'style="background-color:#f2f2f0;">',
            '<tr><td align="center" style="padding:24px 12px;">',
            '<table role="presentation" border="0" cellpadding="0" cellspacing="0" ',
            f'width="{BODY_WIDTH_PX}" style="width:{BODY_WIDTH_PX}px;max-width:{BODY_WIDTH_PX}px;'
            'background-color:#ffffff;border:1px solid #e2e2df;">',
            '<tr><td style="padding:26px 26px 8px 26px;">',
            f'<div style="{text_style}font-size:12px;letter-spacing:1px;'
            'text-transform:uppercase;color:#5a5a5a;padding:0 0 8px 0;">job.os alert</div>',
            f'<div style="{text_style}font-size:21px;font-weight:bold;">',
            escape(digest.subject),
            "</div>",
            f'<div style="{dim_style}padding:6px 0 0 0;">',
            f"Saved search: {escape(digest.search_name)} &nbsp;&middot;&nbsp; ",
            f"{escape(digest.cadence)} alert</div>",
            "</td></tr>",
            '<tr><td style="padding:18px 26px 0 26px;">',
            _TABLE_OPEN,
            repost_banner,
            *rows,
            overflow_row,
            "</table></td></tr>",
            '<tr><td style="padding:0 26px 22px 26px;">',
            '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">',
            '<tr><td style="border-top:1px solid #e2e2df;padding:16px 0 0 0;">',
            f'<div style="{dim_style}">',
            "How we date these: a job board resets a posting date every time a "
            "recruiter republishes a role, so we treat every date a board gives us "
            "as an estimate and tell you when we first saw the role ourselves.",
            "</div>",
            f'<div style="{dim_style}padding:14px 0 0 0;">',
            f'<a href="{escape(digest.unsubscribe_url)}" style="color:#1a5fd0;">',
            "Unsubscribe from this alert</a> &nbsp;&middot;&nbsp; ",
            f'<a href="{escape(digest.unsubscribe_all_url)}" style="color:#1a5fd0;">',
            "Turn off all job alerts</a>",
            "</div>",
            f'<div style="{dim_style}padding:6px 0 0 0;">',
            "Either link works immediately and needs no sign in.",
            "</div>",
            f'<div style="{dim_style}padding:12px 0 0 0;">',
            escape(digest.postal_address),
            "</div>",
            "</td></tr></table></td></tr>",
            "</table></td></tr></table></body></html>",
        ]
    )


def to_email_message(digest: Digest, *, reply_to: str | None = None) -> EmailMessage:
    """The digest as a message, with the one-click unsubscribe headers.

    RFC 8058: `List-Unsubscribe` carries the HTTPS URI and
    `List-Unsubscribe-Post: List-Unsubscribe=One-Click` tells the mail client it
    may POST to it without asking the user to confirm. Gmail and Yahoo both
    surface a native unsubscribe control when these are present, which is a
    better opt-out than any link in the body.

    RFC 8058 also requires that DKIM cover both headers. That is the sending
    domain's configuration, not ours, and it is on the enablement checklist in
    docs/ALERTS.md.
    """
    return EmailMessage(
        to=digest.recipient,
        subject=digest.subject,
        text=render_text(digest),
        html=render_html(digest),
        headers={
            "List-Unsubscribe": f"<{digest.unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        reply_to=reply_to,
    )


#: Exported for the runner, which needs the same field list when it writes the
#: sent log. Keeping it here means the log and the email cannot disagree about
#: what identifies a job.
DEDUPE_FIELDS: tuple[str, ...] = ("source_key", "content_key")

__all__ = [
    "BODY_WIDTH_PX",
    "CandidateJob",
    "DEDUPE_FIELDS",
    "Digest",
    "DigestJob",
    "SalaryNote",
    "build_digest",
    "content_key",
    "normalize_for_key",
    "render_html",
    "render_text",
    "salary_note",
    "source_key",
    "to_email_message",
]
