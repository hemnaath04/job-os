"""Where a target person's name and email address come from.

Finding people and their addresses is the half of outreach with real legal
exposure, so it is behind an interface with exactly one implementation: the user
pastes what they found themselves. That makes the feature useful today with no
third-party dependency, no terms to accept and no bill, and it leaves the
enrichment slot open.

`docs/contact-data-providers.md` is the decision memo behind that: Proxycurl was
shut down in July 2025 after LinkedIn sued its parent, Apollo's API terms forbid
integrating it into a product without written approval, and the recommendation
when discovery does get built is Firecrawl on the company's own team page first,
then Hunter.io on its free tier. Adding Hunter means one class here and one entry
in `_PROVIDERS`.

Nothing in this module scrapes LinkedIn, and nothing here should ever be made to.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog

from job_os.db.models.outreach import ContactRelationship, EmailSource
from job_os.services.identity import identity_text

log = structlog.get_logger(__name__)

# Deliberately not a pydantic-settings field. Two sibling branches appending to
# `Settings` conflict on the same lines, and this is one optional knob for a
# registry that currently holds one entry. It moves into Settings the day a real
# provider needs an API key, because that key does belong there.
_PROVIDER_ENV_VAR = "CONTACT_PROVIDER"

# Good enough to reject a typo, not an attempt to validate the world's addresses.
# The real deliverability question belongs to a verifier, and today the user has
# already confirmed the address by finding it.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

# A LinkedIn person URL, which is the only LinkedIn shape this codebase stores.
# Stored as a link for the user to click, never fetched.
_LINKEDIN_RE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$", re.I
)


class ContactProviderError(ValueError):
    """A submission or lookup that cannot be honoured, with a reason to show."""


@dataclass(frozen=True)
class ContactQuery:
    """What we know before we know who to talk to."""

    company_name: str | None = None
    company_domain: str | None = None
    job_title: str | None = None
    # A named person, when the user already has the name and wants only an
    # address. Hunter's Email Finder takes exactly this shape.
    full_name: str | None = None


@dataclass(frozen=True)
class ContactCandidate:
    """One person a provider believes works at the target company.

    `source`, `email_source`, `confidence` and `evidence_url` travel together on
    purpose. An address read off a company's own team page and one inferred from
    a domain pattern are different evidence, and a candidate that cannot say
    which it is has no business reaching the drafting step.
    """

    full_name: str
    source: str
    title: str | None = None
    company_name: str | None = None
    email: str | None = None
    email_source: EmailSource | None = None
    confidence: int | None = None
    linkedin_url: str | None = None
    evidence_url: str | None = None
    relationship: ContactRelationship = ContactRelationship.OTHER
    # What the user asserts they have in common with this person. Carried here so
    # a future provider can populate it, and checked against the verified vault
    # before any of it reaches a message. See `outreach.shared_context`.
    shared_school: str | None = None
    shared_employer: str | None = None
    referred_by: str | None = None

    @property
    def identity_key(self) -> str:
        """What makes this the same person as an existing row.

        The address when there is one, since two spellings of a name with one
        address are one person. Otherwise the folded name.
        """
        if self.email:
            return self.email.strip().casefold()
        return identity_text(self.full_name)


@runtime_checkable
class ContactProvider(Protocol):
    """The seam a real enrichment vendor slots into later.

    One method, because discovery only ever answers one question. A provider that
    can only enrich a known name returns candidates for that name; one that can
    search a company returns several.
    """

    id: str

    async def find(self, query: ContactQuery) -> list[ContactCandidate]:
        """People matching `query`, best first. An empty list is a valid answer."""
        ...


class ManualContactProvider:
    """The user is the provider. The only implementation today.

    `find` returns nothing, which is honest rather than lazy: this provider does
    not search, the user does. `accept` is the write path, and it is where a
    pasted name and address get normalised and checked before they are stored.
    """

    id = "manual"

    async def find(self, query: ContactQuery) -> list[ContactCandidate]:
        """No search. The user brings the name; there is nothing to look up."""
        log.debug("contact_provider.manual_find_noop", company=query.company_name)
        return []

    def accept(
        self,
        *,
        full_name: str,
        title: str | None = None,
        company_name: str | None = None,
        email: str | None = None,
        linkedin_url: str | None = None,
        evidence_url: str | None = None,
        relationship: ContactRelationship = ContactRelationship.OTHER,
        shared_school: str | None = None,
        shared_employer: str | None = None,
        referred_by: str | None = None,
    ) -> ContactCandidate:
        """Normalise one pasted contact into a candidate, or refuse it.

        Refuses rather than storing something unusable: a blank name gives the
        drafting step nothing to address, and a malformed address is a bounce the
        user would only find out about after sending.
        """
        name = " ".join((full_name or "").split())
        if not name:
            raise ContactProviderError("A name is required to draft a message.")

        cleaned_email = (email or "").strip() or None
        if cleaned_email and not _EMAIL_RE.match(cleaned_email):
            raise ContactProviderError(f"{cleaned_email!r} is not a valid email address.")

        cleaned_linkedin = (linkedin_url or "").strip() or None
        if cleaned_linkedin and not _LINKEDIN_RE.match(cleaned_linkedin):
            raise ContactProviderError(
                "A LinkedIn URL should look like https://www.linkedin.com/in/their-handle"
            )

        return ContactCandidate(
            full_name=name,
            source=self.id,
            title=" ".join((title or "").split()) or None,
            company_name=" ".join((company_name or "").split()) or None,
            email=cleaned_email,
            # The user found it, so the user is the source. Never claim a
            # provider verified an address nobody verified.
            email_source=EmailSource.USER_PROVIDED if cleaned_email else None,
            # No number, on purpose. A confidence score means a provider measured
            # something; inventing 100 here would be the same lie in the other
            # direction.
            confidence=None,
            linkedin_url=cleaned_linkedin,
            evidence_url=(evidence_url or "").strip() or None,
            relationship=relationship,
            shared_school=" ".join((shared_school or "").split()) or None,
            shared_employer=" ".join((shared_employer or "").split()) or None,
            referred_by=" ".join((referred_by or "").split()) or None,
        )


_PROVIDERS: dict[str, ContactProvider] = {
    ManualContactProvider.id: ManualContactProvider(),
}


def get_contact_provider(name: str | None = None) -> ContactProvider:
    """The configured provider, falling back to manual entry.

    An unknown name falls back rather than raising: a typo in an environment
    variable should not take the whole feature down when there is a working
    default sitting right there.
    """
    requested = (name or os.environ.get(_PROVIDER_ENV_VAR) or ManualContactProvider.id).strip()
    provider = _PROVIDERS.get(requested)
    if provider is None:
        log.warning(
            "contact_provider.unknown_falling_back",
            requested=requested,
            known=sorted(_PROVIDERS),
        )
        return _PROVIDERS[ManualContactProvider.id]
    return provider


def manual_provider() -> ManualContactProvider:
    """The manual provider specifically, for the paste-a-contact write path.

    Separate from `get_contact_provider` because accepting a pasted contact is
    always available, whatever search provider happens to be configured.
    """
    return _PROVIDERS[ManualContactProvider.id]  # type: ignore[return-value]
