"""Natural-language → DiscoverySearchRequest via Claude (Manifest gateway).

The user types a free-form sentence (e.g. "fullstack internship in Boston with
Python and React, posted in the last 2 weeks"). Claude returns a structured
DiscoverySearchRequest the FE can hydrate the form with + execute.

The prompt is written against how the filters are actually applied downstream,
which is the part that decides whether a search returns anything:

  - title_keywords does the real work. Every source honours it, and for the
    key-free ATS boards it is the ONLY filter that narrows anything, because
    those boards are fetched whole and filtered here. Each entry is matched
    word-by-word against the title, so a longer phrase is a stricter one, and
    several short alternatives beat one long restatement of the sentence.
  - technology_slugs and country_codes are honoured by some sources and
    ignored by others, so a role word must never be moved out of
    title_keywords into them.
  - country_codes stays empty unless the user named a place. An empty list
    searches everywhere; a wrong code hides everything.
  - max_age_days defaults to 30, limit to 20.
  - sources is left empty. The UI toggles own that choice.

Runs on the fast tier (Haiku): this is short structured extraction on one
sentence, not reasoning, and the user is waiting on it before the search even
starts.
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

HOW title_keywords IS ACTUALLY MATCHED. Read this before writing any.
Each entry is a phrase. A posting matches a phrase when EVERY word in that
phrase appears somewhere in the job title, in any order, punctuation ignored.
The entries are alternatives: matching any one of them is enough.

So "ai engineer intern" matches "AI/ML Engineer Intern" and "Software Engineer
Intern, AI", but NOT "Machine Learning Engineer Intern", because the word "ai"
is not in that title.

Two consequences, and they decide whether the search returns anything:
- Every extra word in a phrase makes it STRICTER. Three words is usually the
  most a real job title will satisfy. Four is rarely worth it.
- Employers title the same role many different ways, so give the alternatives.
  One phrase per way the role is actually written, 3 to 6 of them.

For "AI engineer internship" a good answer is:
  ["ai engineer intern", "ai intern", "machine learning intern",
   "ml engineer intern", "ai engineer co-op"]
and a bad answer is:
  ["ai engineer internship 2027"]
because no posting is titled that, and it would return nothing.

Rules for the rest:
1. title_keywords: lower-case. Cover synonyms the user did not type:
   intern / internship / co-op, new grad / entry level / university graduate,
   ml / machine learning, ai / artificial intelligence. NEVER include a
   company name, a location, or a year. Years over-constrain the title and
   recency is handled by max_age_days.
2. technology_slugs: lower-case canonical names the user actually named
   (python, react, fastapi, pytorch, kubernetes). Skip broad words like "AI"
   or "ML", which belong in title_keywords. NOTE: only some sources filter on
   this, so never move a role word out of title_keywords into here.
3. country_codes: only if the user named a country or region. ISO 3166
   alpha-2 (US, CA, GB, IN). A US city implies US. If unspecified, leave
   empty, since an empty list searches everywhere and a wrong code hides
   everything.
4. max_age_days: integer, default 30. Raise only if the user asked for a
   wider window.
5. limit: integer, default 20, cap 50.
6. sources: leave empty. The user picks sources with toggles in the UI and
   their choice wins over anything you put here.

WHEN IN DOUBT, SEARCH WIDER. An empty result page tells the user nothing,
while extra results are ranked by fit against their profile and cost them one
scroll. Prefer the shorter phrase and the extra synonym.

Output schema:
{schema}

Also include a one-line `explanation` of what you extracted, in plain language
("Interns and co-ops for AI and ML engineering roles, posted in the last 30
days"). If you dropped something the user said, such as a graduation year, say
so there.
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
