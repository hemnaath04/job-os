"""The evidence vault now arrives over HTTP, so its shape is not guaranteed.

`_compact_facts` used to be fed only by our own workspace loader, which produces
well-formed rows. `/resumes/render-review` now accepts `verified_facts` from the
browser, which makes this ordinary untrusted input: a payload serialised as a
JSON string, a bullet that is not an object, a null where a list was expected.

Raising on any of those would 500 the whole request and take the PDF render down
along with the review, when the right answer is to use what is well formed and
ignore what is not. What must not happen is silently dropping good evidence,
because missing evidence is what makes the reviewer grade real history as
invented in the first place.
"""
from __future__ import annotations

import os
from typing import Any

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services.resume_engine import _compact_facts  # noqa: E402

GOOD: dict[str, Any] = {
    "kind": "project",
    "title": "BedRocked",
    "org": None,
    "payload": {"keywords": ["Python"]},
    "bullets": [{"text": "Scored 2,404 sewer segments."}],
}


def test_a_well_formed_vault_is_unchanged() -> None:
    [entry] = _compact_facts([GOOD])
    assert entry["title"] == "BedRocked"
    assert entry["bullets"] == ["Scored 2,404 sewer segments."]
    assert entry["payload"] == {"keywords": ["Python"]}


def test_a_payload_that_arrived_as_a_string_does_not_raise() -> None:
    # A browser that forgot to parse the snapshot would send this.
    fact = {"kind": "skill", "title": "RAG", "org": "AI / ML", "payload": '{"a": 1}'}
    [entry] = _compact_facts([fact])
    # Falls back to the org for the category rather than exploding.
    assert entry == {"kind": "skill", "title": "RAG", "category": "AI / ML"}


def test_junk_entries_are_skipped_not_fatal() -> None:
    assert _compact_facts(["nonsense", 42, None]) == []  # type: ignore[list-item]


def test_a_malformed_bullet_does_not_cost_the_good_ones() -> None:
    fact = {**GOOD, "bullets": [{"text": "kept"}, "junk", None, {"text": ""}]}
    [entry] = _compact_facts([fact])
    assert entry["bullets"] == ["kept"]


def test_bullets_that_are_not_a_list_are_ignored() -> None:
    [entry] = _compact_facts([{**GOOD, "bullets": "oops"}])
    assert "bullets" not in entry
    assert entry["title"] == "BedRocked"


def test_good_facts_survive_alongside_bad_ones() -> None:
    # The whole point: one malformed row must not cost the reviewer the rest of
    # the vault, because an empty vault is what produces false fabrication calls.
    facts = ["junk", {**GOOD, "payload": "not json"}, GOOD]
    compact = _compact_facts(facts)  # type: ignore[arg-type]
    assert len(compact) == 2
    assert all(entry["title"] == "BedRocked" for entry in compact)
