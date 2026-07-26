"""Natural-language → DiscoverySearchRequest via Claude (Manifest gateway).

The user types a free-form sentence (e.g. "fullstack internship in Boston with
Python and React, posted in the last 2 weeks"). Claude returns a structured
DiscoverySearchRequest the FE can hydrate the form with + execute.

Hard rules baked into the prompt:
  - Don't invent country codes outside the user's sentence. If they didn't
    specify, leave country_codes empty.
  - Title keywords are short phrases (1-4 words each), lower-cased.
  - Technology slugs are lower-case canonical names (python, react, fastapi,
    pytorch, kubernetes, ...). Don't echo company names or random nouns.
  - max_age_days defaults to 30 unless the user said otherwise.
  - Always include both sources (theirstack + github) unless the user told
    you to narrow.
"""
from __future__ import annotations

import json

import anthropic
import structlog
from pydantic import ValidationError

from job_os.schemas.discovery import (
    DiscoverySearchRequest,
    SmartSearchResponse,
)
from job_os.services.jd_parse import _strip_json_fence
from job_os.settings import get_settings

log = structlog.get_logger(__name__)


SYSTEM_PROMPT = """\
You translate a user's free-form job-search sentence into a structured
DiscoverySearchRequest. Return ONLY a single JSON object matching the
provided schema. No prose, no fences.

Hard rules:
1. title_keywords: 1-4 short phrases, lower-cased, comma-separated as
   array entries. Capture the role (e.g. "software engineer intern",
   "data scientist new grad"). NEVER include company names here.
2. technology_slugs: lower-case canonical tech names mentioned in the
   sentence (python, react, fastapi, pytorch, kubernetes, etc.). Only
   include techs the user named. Skip generic words like "AI" or "ML"
   unless they're explicit framework slugs.
3. country_codes: only include if the user gave a country/region. Use
   ISO 3166 alpha-2 (US, CA, GB, IN, ...). If they said a US city,
   country = US. If unspecified, leave empty.
4. max_age_days: integer, default 30. Bump higher only if the user
   explicitly says "last month", "this year", etc.
5. limit: integer, default 20. Cap at 50.
6. sources: ["theirstack", "github"] unless the user told you to limit.

Output schema:
{schema}

Also include a one-line `explanation` describing what you extracted
("Looking for fullstack interns in Boston with React + Python").
"""


async def parse_smart_query(query: str) -> SmartSearchResponse:
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.warning("smart_search.no_anthropic_key")
        # Graceful fallback: return an empty filter with the raw query as a
        # single title keyword so the FE can still run something.
        return SmartSearchResponse(
            filters=DiscoverySearchRequest(title_keywords=[query.strip()]),
            explanation="(LLM not configured — using your text as the title keyword.)",
        )

    client = anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )

    user_prompt = (
        f"User sentence:\n<query>\n{query}\n</query>\n\n"
        "Extract the filters. Respond with a single JSON object matching the schema."
    )

    msg = await client.messages.create(
        model=settings.anthropic_model_extract,
        max_tokens=1024,
        system=SYSTEM_PROMPT.format(
            schema=json.dumps(SmartSearchResponse.model_json_schema())
        ),
        messages=[{"role": "user", "content": user_prompt}],
        extra_headers={"x-manifest-tier": settings.manifest_tier_fast},
    )

    text = "".join(b.text for b in msg.content if b.type == "text")
    raw = _strip_json_fence(text)
    try:
        return SmartSearchResponse.model_validate_json(raw)
    except ValidationError as e:
        log.warning("smart_search.invalid_json", error=str(e), preview=raw[:300])
        return SmartSearchResponse(
            filters=DiscoverySearchRequest(title_keywords=[query.strip()]),
            explanation="(Couldn't parse the query — using the raw text as a fallback.)",
        )
