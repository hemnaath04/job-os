"""Deciding when two pieces of career history describe the same thing.

Used in two places that must agree: importing a resume, where it decides whether
a fact already exists, and rendering a tailored resume, where it decides whether
two entries are one job. Divergence between them is what produced a resume
listing the same employer twice.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# A date range identifies these outright. One person holding one job at one
# employer over one span is one fact however the title happens to be worded, and
# rewording is exactly what a second import of the same resume produces.
DATE_KEYED_KINDS = frozenset({"experience", "education"})


def identity_text(value: Any) -> str:
    """Fold a name down to what identifies it, ignoring how it was punctuated.

    "Northeastern University - Khoury College" and "Northeastern University,
    Khoury College" are the same institution, so strip accents, case and every
    run of non-alphanumerics before comparing.
    """
    folded = unicodedata.normalize("NFKD", str(value or "")).casefold()
    # Drop the combining marks NFKD split off, rather than letting the regex
    # below turn each one into a space. Otherwise "Crème" normalises to
    # "cre me" and stops matching "Creme", which is the opposite of the point.
    unaccented = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unaccented).split())


def fact_identity(fact: dict[str, Any]) -> tuple[str, ...]:
    """The key that decides whether an imported fact already exists.

    Keyed on the date range for jobs and degrees, since a re-import routinely
    rewords a title ("Junior Software Test Automation Engineer, Client: ..."
    against "Software Test Automation Engineer") and a title-keyed check treats
    that as a new job. Everything else keys on its title, because a skill or a
    certificate has no span to identify it by.
    """
    kind = str(fact.get("kind") or "")
    org = identity_text(fact.get("org"))
    if kind in DATE_KEYED_KINDS:
        return (
            kind,
            org,
            str(fact.get("start_date") or "")[:10],
            str(fact.get("end_date") or "")[:10],
        )
    return (kind, org, identity_text(fact.get("title")))
