"""The model was reading a third of his vault, and none of his projects.

`json.dumps(payload, indent=2)[:12000]` is three mistakes in one line.

His vault serialises that way to 39,848 characters, so 30% of it reached the
compose and analyst calls. Facts are written in kind order and projects come
last, so EVERY project fell past the cut. And a mid-string slice of a JSON blob
is not shortened JSON, it is broken JSON.

That is why run after run reported his fact data "was truncated in the profile
feed", and why job.os kept missing the page whatever the ranking said: it was
never in the feed the writer chose from.
"""
from __future__ import annotations

import json

from job_os.services.tailor import FACTS_PAYLOAD_BUDGET, _facts_feed


def vault(n_skills: int = 90) -> list[dict]:
    projects = [
        {
            "id": f"p{i}",
            "kind": "project",
            "title": t,
            "payload": {"keywords": ["Python", "FastAPI", "LLM Integration"]},
            "bullets": [{"id": f"b{i}", "text": "Built a thing that does a thing."}],
        }
        for i, t in enumerate(("job.os", "ClaimFarm", "BedRocked"))
    ]
    skills = [
        {"id": f"s{i}", "kind": "skill", "title": f"Skill {i}", "org": "Cat",
         "payload": {"level": None, "category": "Cat"}, "bullets": []}
        for i in range(n_skills)
    ]
    # Skills first, projects last: the order that made projects the casualty.
    return skills + projects


def test_the_feed_is_valid_json() -> None:
    """A mid-string cut produced something the model could not parse at all."""
    assert isinstance(json.loads(_facts_feed(vault())), list)


def test_every_project_reaches_the_model() -> None:
    feed = json.loads(_facts_feed(vault()))
    titles = {f.get("title") for f in feed if isinstance(f, dict)}
    for project in ("job.os", "ClaimFarm", "BedRocked"):
        assert project in titles, f"{project} never reached the writer"


def test_empty_fields_are_dropped_because_they_were_most_of_the_bytes() -> None:
    feed = _facts_feed(vault(n_skills=1))
    assert '"level":null' not in feed
    assert "null" not in feed


def test_a_vault_that_genuinely_does_not_fit_drops_whole_facts_not_characters() -> None:
    """Never a mid-token cut, and never silently."""
    feed = _facts_feed(vault(n_skills=6000))
    parsed = json.loads(feed)
    assert isinstance(parsed, list), "still parseable"
    assert len(feed) <= FACTS_PAYLOAD_BUDGET + 200
    assert any("_note" in f for f in parsed if isinstance(f, dict)), "says what went"


def test_projects_survive_even_when_the_vault_overflows() -> None:
    """Skills are a name and a category; a project is evidence."""
    parsed = json.loads(_facts_feed(vault(n_skills=6000)))
    titles = {f.get("title") for f in parsed if isinstance(f, dict)}
    for project in ("job.os", "ClaimFarm", "BedRocked"):
        assert project in titles
