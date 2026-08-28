"""Which kind of engineering a posting is for, and which kind a project shows.

`_project_relevance` in tailor.py counts how many of a posting's requirements a
project's own text names. That is the right primary signal and it is blind to
one thing a human reader is not: what the job actually IS.

A platform posting that mentions computer vision once produces one requirement
saying "computer vision", and a vision side project matches it. So does a
backend project match "Python" and "APIs". Two projects, one requirement each,
tied, and the tie was then broken by whether a URL was recorded. That is how a
candidate's strongest backend work came off a backend posting because the
posting happened to name a model architecture in its "you may also touch"
paragraph.

Lanes fix the tie and nothing else. They never move a project across a
requirement-count tier, because the count is still the better evidence when the
two disagree; they decide which of two equally-matching projects goes on the
page. See `_evidence_rank` for where that happens.

Everything here is vocabulary, not a model, and it is deliberately about the
work rather than about any one person's projects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The lanes, and the words that mean each one. Chosen to be things a job
# description and a project description both actually say. A word that names a
# whole industry ("fintech") or a soft quality ("scalable") is left out: it
# appears in every posting and separates nothing.
#
# Overlap between lanes is expected and fine -- "pipeline" is real in both data
# and platform work -- because the classification is a comparison of totals, not
# a single-word decision.
LANE_TERMS: dict[str, tuple[str, ...]] = {
    "backend": (
        "backend", "back end", "back-end", "server side", "server-side",
        "api", "apis", "rest", "restful", "grpc", "graphql", "microservice",
        "microservices", "service", "services", "distributed", "concurrency",
        "concurrent", "throughput", "latency", "scalability", "load balancing",
        "database", "databases", "sql", "postgres", "postgresql", "mysql",
        "redis", "cache", "caching", "queue", "kafka", "rabbitmq", "message broker",
        "kubernetes", "docker", "container", "containers", "terraform",
        "infrastructure", "platform", "systems", "system design", "runtime",
        "compiler", "operating system", "networking", "storage", "sharding",
        "replication", "observability", "sre", "reliability", "deployment",
        "ci/cd", "devops", "webhook", "authentication", "authorization",
        "fastapi", "django", "flask", "spring", "express", "node.js",
    ),
    "ml": (
        "machine learning", "deep learning", "neural network", "neural networks",
        "model training", "training", "inference", "fine-tuning", "fine tuning",
        "pytorch", "tensorflow", "jax", "keras", "scikit-learn", "sklearn",
        "computer vision", "image", "images", "vision", "opencv", "segmentation",
        "object detection", "detection", "classification", "classifier",
        "embedding", "embeddings", "transformer", "transformers", "llm", "llms",
        "large language model", "nlp", "natural language", "reinforcement learning",
        "recommendation", "recommender", "ranking model", "feature engineering",
        "hyperparameter", "cuda", "gpu", "quantization", "rag",
        "retrieval-augmented", "prompt engineering", "agentic", "agents",
    ),
    "data": (
        "etl", "elt", "data pipeline", "data pipelines", "data warehouse",
        "warehouse", "lakehouse", "data lake", "spark", "hadoop", "airflow",
        "dbt", "snowflake", "bigquery", "redshift", "analytics", "dashboard",
        "dashboards", "reporting", "bi", "pandas", "numpy", "data modeling",
        "data modelling", "data quality", "ingestion", "batch processing",
        "stream processing", "streaming", "olap", "data engineering",
    ),
    "test": (
        "test automation", "automated testing", "automated tests", "qa",
        "quality assurance", "test suite", "test suites", "regression testing",
        "regression suite", "unit tests", "integration tests", "end to end tests",
        "selenium", "playwright", "cypress", "appium", "pytest", "junit",
        "testng", "test coverage", "flaky", "test framework", "sdet",
        "test plan", "test cases", "manual testing", "load testing",
    ),
    "frontend": (
        "frontend", "front end", "front-end", "react", "next.js", "vue",
        "angular", "svelte", "typescript", "javascript", "css", "tailwind",
        "responsive design", "accessibility", "wcag", "browser", "dom",
        "component library", "design system", "ux", "ui component",
    ),
}

# A posting only HAS a single lane when one lane clearly leads. A generic
# "software engineer, intern" posting names a little of everything, and forcing
# a lane out of a two-word margin would reorder projects on noise. Both
# conditions apply: the leader needs this many hits at all, and this much more
# than the runner-up.
_MIN_LANE_HITS = 3
_MIN_LANE_MARGIN = 2

_WORD_BOUNDARY_SAFE = re.compile(r"^[\w.+#/ -]+$")


@dataclass(frozen=True)
class LaneProfile:
    """How much of each lane a piece of text talks about."""

    hits: dict[str, int]

    @property
    def leader(self) -> str | None:
        """The one lane this text is about, or None when it is not about one."""
        if not self.hits:
            return None
        ranked = sorted(self.hits.items(), key=lambda item: (-item[1], item[0]))
        top_lane, top_hits = ranked[0]
        if top_hits < _MIN_LANE_HITS:
            return None
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if top_hits - runner_up < _MIN_LANE_MARGIN:
            return None
        return top_lane

    @property
    def lanes(self) -> tuple[str, ...]:
        """Every lane this text is genuinely about, not just the one that leads.

        A full-stack posting is about two kinds of work, and reading it as one
        was a real bug: `leader` picks a single winner, so "React and
        TypeScript on the front end, Python services behind them" came back as
        a backend role, and the candidate's product/UI project was then the one
        `_evidence_rank` dropped first on a tie with a backend project. The
        posting had asked for both in the same breath.

        A lane qualifies when it clears `_MIN_LANE_HITS` and sits within
        `_MIN_LANE_MARGIN` of the top lane, which is the exact complement of
        the `leader` rule: where the margin is wide enough for one lane to
        win outright, only that lane is returned, and where it is not, the
        lanes that were too close to separate all count. A platform posting
        naming computer vision once is still 12 hits to 3, so it stays a
        single-lane posting.

        Returned in hit order, strongest first, ties alphabetical, so the
        label a reader is shown is stable across runs.
        """
        ranked = sorted(self.hits.items(), key=lambda item: (-item[1], item[0]))
        qualified = [(lane, n) for lane, n in ranked if n >= _MIN_LANE_HITS]
        if not qualified:
            return ()
        top = qualified[0][1]
        return tuple(lane for lane, n in qualified if top - n <= _MIN_LANE_MARGIN)

    def mentions(self, lane: str) -> int:
        return self.hits.get(lane, 0)


def _pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    # "node.js" and "ci/cd" end in characters `\b` will not anchor against, so
    # the trailing boundary is only asserted where it means something.
    tail = r"\b" if term[-1].isalnum() else ""
    return re.compile(rf"\b{escaped}{tail}", re.I)


_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    lane: tuple(_pattern(term) for term in terms if _WORD_BOUNDARY_SAFE.match(term))
    for lane, terms in LANE_TERMS.items()
}

# `function` on a parsed JD is already a lane judgement made by the extraction
# model against the whole posting, which is better evidence than counting words
# in a truncated slice of it. Counted as if the posting had said the lane's
# vocabulary this many times, so it leads unless the text plainly disagrees.
#
# "swe" is deliberately absent, and its absence is the fix for a real
# misreading. Every mapping here names a lane the function word actually
# commits to: an SRE role is platform work, a research role is model work. A
# "software engineer" is backend, frontend, full-stack or none of them, and
# mapping it to backend put a four-hit thumb on the backend scale for the
# single most common `function` value in the whole index. A full-stack posting
# whose own text was 7 backend to 6 frontend -- genuinely both -- came out as
# a backend role because of it, and the candidate's product/UI project was
# then ranked below their backend one on every tie. The posting's own words
# decide instead, which is what they were already good enough to do.
_FUNCTION_LANES: dict[str, str] = {
    "infra": "backend",
    "sre": "backend",
    "security": "backend",
    "ml": "ml",
    "ai": "ml",
    "research": "ml",
    "data": "data",
    "design": "frontend",
}
_FUNCTION_WEIGHT = 4


def lane_profile(text: str) -> LaneProfile:
    """Count each lane's vocabulary in one blob of text."""
    blob = text or ""
    if not blob.strip():
        return LaneProfile(hits={})
    hits = {
        lane: sum(1 for pattern in patterns if pattern.search(blob))
        for lane, patterns in _COMPILED.items()
    }
    return LaneProfile(hits={lane: n for lane, n in hits.items() if n})


def _jd_profile(jd_parsed: dict[str, Any] | None, jd_clean: str | None) -> LaneProfile:
    """Lane vocabulary counted over everything the posting says about itself.

    Reads the parsed fields and the posting's own text together. The title is
    counted twice on purpose: "Backend Engineer Intern, Computer Vision Platform"
    is a backend job, and the title is the part of a posting that says so.
    """
    parsed = jd_parsed or {}
    title = str(parsed.get("title") or "")
    listed = " ".join(
        str(item)
        for key in ("required_skills", "preferred_skills", "technologies", "keywords",
                    "qualifications", "responsibilities")
        for item in (parsed.get(key) or [])
    )
    profile = lane_profile(
        " ".join([title, title, listed, str(jd_clean or "")])
    )
    hits = dict(profile.hits)
    mapped = _FUNCTION_LANES.get(str(parsed.get("function") or "").casefold())
    if mapped:
        hits[mapped] = hits.get(mapped, 0) + _FUNCTION_WEIGHT
    return LaneProfile(hits=hits)


def jd_lane(jd_parsed: dict[str, Any] | None, jd_clean: str | None) -> str | None:
    """The one lane this posting is hiring for, or None when it names more than
    one or commits to none.

    Kept alongside `jd_lanes` because a single name is what reads naturally in
    a sentence ("this posting is a backend role"), and there is no honest
    single name for a full-stack posting.
    """
    return _jd_profile(jd_parsed, jd_clean).leader


def jd_lanes(
    jd_parsed: dict[str, Any] | None, jd_clean: str | None
) -> tuple[str, ...]:
    """Every kind of work this posting is hiring for.

    Usually one, sometimes two: a full-stack posting names backend and product
    /UI work in the same breath and is hiring for both, so a project in either
    is the same kind of work as the job. Empty when the posting commits to
    nothing, which leaves project ranking exactly where it was before lanes
    existed. See `LaneProfile.lanes`.
    """
    return _jd_profile(jd_parsed, jd_clean).lanes


def text_lanes(text: str) -> frozenset[str]:
    """Every kind of work one blob of text is about.

    The project side of the same question `jd_lanes` answers for a posting, and
    deliberately the same rule: a project that is genuinely half UI and half
    service work should match a posting that is genuinely both, which a
    single-winner reading cannot express.
    """
    return frozenset(lane_profile(text).lanes)
