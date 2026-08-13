"""Import a JSON Resume document into profile_facts + fact_bullets.

The mapping is intentionally conservative — every bullet imported is a
verbatim copy of a `highlights[]` entry from the source document, so the
no-hallucination invariant starts from a clean baseline.

Re-running the import is idempotent: facts are keyed by (kind, org, title);
existing rows are skipped, never overwritten. Use `replace_existing=True`
to nuke and re-import.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models import FactBullet, ProfileFact, Resume, ResumeVersion, User
from job_os.schemas.profile import ImportReport
from job_os.services.embeddings import embed_many

log = structlog.get_logger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    parts = value.split("-")
    if len(parts) == 1:
        return date(int(parts[0]), 1, 1)
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1)
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def _key(kind: str, org: str | None, title: str) -> tuple[str, str, str]:
    return (kind, (org or "").strip().lower(), title.strip().lower())


def _contact_fact(doc: dict[str, Any]) -> ProfileFact | None:
    """One `contact` fact carrying JSON Resume `basics`.

    Everything an application form asks for before it asks anything interesting,
    including name, email, phone, address, LinkedIn, GitHub, lives here, so the
    autofill extension can read it from the same vault as everything else and
    get the same `verified` gate for free. Values are copied verbatim; the
    `profiles[]` array is flattened to network -> url because picking a format
    for a URL we did not receive would be inventing one.

    Returns None when `basics` has no name, since a contact card with no owner
    is not worth a row.
    """
    basics = doc.get("basics") or {}
    name = (basics.get("name") or "").strip()
    if not name:
        return None

    location = basics.get("location") or {}
    profiles: dict[str, str] = {}
    for entry in basics.get("profiles", []) or []:
        network = (entry.get("network") or "").strip().lower()
        url = (entry.get("url") or "").strip()
        if network and url:
            profiles[network] = url

    return ProfileFact(
        kind="contact",
        title=name,
        org=None,
        location=location.get("city"),
        source_url=(basics.get("url") or "").strip() or None,
        payload={
            "name": name,
            "label": basics.get("label"),
            "email": basics.get("email"),
            "phone": basics.get("phone"),
            "url": basics.get("url"),
            "address": location.get("address"),
            "city": location.get("city"),
            "region": location.get("region"),
            "postalCode": location.get("postalCode"),
            "countryCode": location.get("countryCode"),
            "profiles": profiles,
        },
    )


def _facts_from_json_resume(doc: dict[str, Any]) -> list[tuple[ProfileFact, list[str]]]:
    """Yield (fact, bullets[]) pairs. Skills get one fact each, no bullets."""
    out: list[tuple[ProfileFact, list[str]]] = []

    # Contact card (JSON Resume `basics`)
    contact = _contact_fact(doc)
    if contact is not None:
        out.append((contact, []))

    # Education
    for entry in doc.get("education", []) or []:
        fact = ProfileFact(
            kind="education",
            title=f"{entry.get('studyType', '')} {entry.get('area', '')}".strip() or "Education",
            org=entry.get("institution"),
            start_date=_parse_date(entry.get("startDate")),
            end_date=_parse_date(entry.get("endDate")),
            location=entry.get("location"),
            source_url=entry.get("url"),
            payload={
                "courses": entry.get("courses", []),
                "score": entry.get("score"),
                "studyType": entry.get("studyType"),
                "area": entry.get("area"),
            },
        )
        out.append((fact, []))

    # Work experience
    for entry in doc.get("work", []) or []:
        fact = ProfileFact(
            kind="experience",
            title=entry.get("position") or entry.get("name") or "Experience",
            org=entry.get("name"),
            start_date=_parse_date(entry.get("startDate")),
            end_date=_parse_date(entry.get("endDate")),
            location=entry.get("location"),
            source_url=entry.get("url"),
            payload={
                "summary": entry.get("summary"),
                "keywords": entry.get("keywords", []),
            },
        )
        out.append((fact, list(entry.get("highlights", []) or [])))

    # Projects
    for entry in doc.get("projects", []) or []:
        fact = ProfileFact(
            kind="project",
            title=entry.get("name") or "Project",
            org=None,
            start_date=_parse_date(entry.get("startDate")),
            end_date=_parse_date(entry.get("endDate")),
            source_url=entry.get("url"),
            payload={
                "description": entry.get("description"),
                "keywords": entry.get("keywords", []),
                "roles": entry.get("roles", []),
                "entity": entry.get("entity"),
                "type": entry.get("type"),
            },
        )
        out.append((fact, list(entry.get("highlights", []) or [])))

    # Skills — one fact per individual skill keyword
    for group in doc.get("skills", []) or []:
        category = group.get("name") or "Skills"
        for kw in group.get("keywords", []) or []:
            fact = ProfileFact(
                kind="skill",
                title=str(kw).strip(),
                org=category,
                payload={"category": category, "level": group.get("level")},
            )
            out.append((fact, []))

    # Certifications
    for entry in doc.get("certificates", []) or []:
        fact = ProfileFact(
            kind="certification",
            title=entry.get("name") or "Certification",
            org=entry.get("issuer"),
            start_date=_parse_date(entry.get("date")),
            source_url=entry.get("url"),
            payload={},
        )
        out.append((fact, []))

    # Publications
    for entry in doc.get("publications", []) or []:
        fact = ProfileFact(
            kind="publication",
            title=entry.get("name") or "Publication",
            org=entry.get("publisher"),
            start_date=_parse_date(entry.get("releaseDate")),
            source_url=entry.get("url"),
            payload={"summary": entry.get("summary")},
        )
        out.append((fact, []))

    # Awards
    for entry in doc.get("awards", []) or []:
        fact = ProfileFact(
            kind="award",
            title=entry.get("title") or "Award",
            org=entry.get("awarder"),
            start_date=_parse_date(entry.get("date")),
            payload={"summary": entry.get("summary")},
        )
        out.append((fact, []))

    # Volunteering
    for entry in doc.get("volunteer", []) or []:
        fact = ProfileFact(
            kind="volunteering",
            title=entry.get("position") or entry.get("organization") or "Volunteering",
            org=entry.get("organization"),
            start_date=_parse_date(entry.get("startDate")),
            end_date=_parse_date(entry.get("endDate")),
            source_url=entry.get("url"),
            payload={"summary": entry.get("summary")},
        )
        out.append((fact, list(entry.get("highlights", []) or [])))

    return out


async def import_json_resume(
    session: AsyncSession,
    *,
    user: User,
    doc: dict[str, Any],
    mark_verified: bool = True,
    replace_existing: bool = False,
) -> ImportReport:
    report = ImportReport(facts_created=0, facts_skipped=0, bullets_created=0, bullets_embedded=0)

    if replace_existing:
        await session.execute(delete(ProfileFact).where(ProfileFact.user_id == user.id))
        await session.flush()
        report.notes.append("Replaced all existing profile facts.")

    existing = (
        await session.execute(
            select(ProfileFact.kind, ProfileFact.org, ProfileFact.title).where(
                ProfileFact.user_id == user.id
            )
        )
    ).all()
    existing_keys = {_key(k, o, t) for k, o, t in existing}

    pending_bullets: list[FactBullet] = []
    pending_texts: list[str] = []

    for fact, bullets in _facts_from_json_resume(doc):
        key = _key(fact.kind, fact.org, fact.title)
        if key in existing_keys:
            report.facts_skipped += 1
            continue
        fact.user_id = user.id
        fact.verified = mark_verified
        session.add(fact)
        report.facts_created += 1
        existing_keys.add(key)

        await session.flush()  # need fact.id before bullets

        for text in bullets:
            bullet = FactBullet(fact_id=fact.id, text=text, metric_verified=True)
            session.add(bullet)
            pending_bullets.append(bullet)
            pending_texts.append(text)
            report.bullets_created += 1

    # Embed all new bullets in one batch (cheaper). Tolerates no-key by no-op.
    if pending_texts:
        vectors = await embed_many(pending_texts)
        for bullet, vec in zip(pending_bullets, vectors, strict=True):
            bullet.embedding = vec
            if vec is not None:
                report.bullets_embedded += 1

    # Auto-create the Master resume + v1 from the imported document so /resumes
    # is populated by the same upload action. Idempotent: skip if a master
    # already exists, unless the caller asked to replace.
    master = (
        await session.execute(
            select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True))
        )
    ).scalar_one_or_none()

    if master is None:
        master = Resume(user_id=user.id, name="Master", base_role="master", is_master=True)
        session.add(master)
        await session.flush()
        session.add(
            ResumeVersion(
                resume_id=master.id, json_resume=doc, approved_by_user=True
            )
        )
        report.notes.append("Created Master resume + v1.")
    elif replace_existing:
        session.add(
            ResumeVersion(
                resume_id=master.id, json_resume=doc, approved_by_user=True
            )
        )
        report.notes.append("Appended new version to existing Master resume.")

    return report
