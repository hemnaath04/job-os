"""The company-token corpus.

Token lists live in `seeds/*.txt` inside the package, one token per line. They
are bundled rather than read from a path outside the repo so the build and the
crawl do not depend on a developer's home directory.

    greenhouse.txt        8,333 tokens
    lever.txt             4,368 tokens
    ashby.txt             3,161 tokens
    smartrecruiters.txt       9 tokens
    workday.txt              13 tokens
    icims.txt                31 tokens
    oracle_cloud.txt         12 tokens
    curated.json             97 companies, with real employer names and domains

`workday.txt`, `icims.txt` and `oracle_cloud.txt` are the exceptions to the
sentence below: every token in all three answered 200 with jobs on 2026-08-30.
None of them is guessable, and each is unguessable in its own way.

A Workday token is `tenant:datacenter:site`, and there is nothing to derive it
from: a wrong site on a real tenant returns 404, a wrong tenant returns 422. An
unverified Workday token is simply a dead row. 12 further candidates were tried
and dropped for exactly that reason.

An iCIMS token is a subdomain (`careers-here` from careers-here.icims.com), and
the prefix varies per tenant (`careers-`, `jobs-`, `uscareers-`, `staff-`,
`clinical-`, `securitycareers-`, `us-`), so the 31 here came from search results
and were then each fetched. Of 65 candidates probed, 60 served a usable sitemap,
2 had opted out via `robots.txt: Disallow: /`, 1 subdomain did not exist, 1 had
migrated off iCIMS entirely, and 1 listed no job URLs. The 31 kept are a
size-spread slice of the 60, from 31 job URLs (`jobs-getty`) to 8,804
(`securitycareers-aus`).

Oracle is worse than dead, which is why its 12 tokens were each checked twice.
A wrong Oracle tenant answers 504 from Oracle's edge, so it never prunes and
just burns three requests a sweep forever. A wrong *site* on a real tenant does
not fail at all: it returns the tenant's whole unfiltered requisition pool,
measured at exactly the sum of the real sites (Goldman Sachs 1012 + 317 + 21 =
1350, which is what `siteNumber=ZZ_NOT_A_SITE` returns). So a typo looks like a
*bigger* board, silently merging pipelines the employer chose to separate. Every
site number here was confirmed to exist via that tenant's `recruitingCESites`
before being written down.

`oracle_cloud` is also the reason `curated.json` grew by 12. Oracle's payload
names no employer at all -- `LegalEmployer`, `Organization` and `BusinessUnit`
were null on every requisition checked across three tenants -- and unlike a
Workday tenant ("nvidia"), an Oracle tenant is an opaque code like "hcgn".
Those 12 rows are what stop 35,565 postings being filed under four-character
strings, and unlike the rest of the file they were written by hand rather than
derived from `ats-companies.ts`, so a regeneration has to carry them across.

**These are seeds, not a verified list.** A 200-token Greenhouse sample measured
on this branch found 61.5% live, which matches the ~62% the research pass
reported, so roughly four tokens in ten are dead on arrival. The corpus is
therefore something to validate and prune, not something to trust: every token
gets a row in `ats_board_tokens`, liveness is recorded per crawl, and dead
tokens stop being re-crawled (see `liveness.py`). Removing them from these files
is deliberately not how that works, because a board can come back and the file
is a record of what we were given.

`curated.json` is generated from the web app's `lib/discover/ats-companies.ts`
and is the only source of real employer names and domains. It matters for dedupe:
company_domain is a far better identity than a board token, since one employer
can hold tokens on two vendors. The bulk corpus has no domain, so those rows fall
back to the name the board reports (Greenhouse sends `company_name`) or to the
token itself.

The SmartRecruiters seed is short and honest about why: the research pass shipped
no SmartRecruiters token list, and the tokens are not derivable from company
names. 120 plausible company names were probed and 9 were live, a 7.5% hit rate,
so guessing is not a strategy. Growing this list needs a real source, and
`docs/ingest-index.md` records the options.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable

from job_os.ingest.providers import PROVIDER_NAMES

# Addressed through the parent package rather than as `job_os.ingest.seeds`, so
# the data directory never has to be an importable package.
_SEED_DIR = "seeds"


def _seed_file(name: str) -> Traversable:
    return resources.files("job_os.ingest").joinpath(_SEED_DIR, name)


@dataclass(frozen=True, slots=True)
class SeedToken:
    provider: str
    token: str
    company_name: str | None = None
    company_domain: str | None = None
    #: Curated companies are crawled first and re-crawled more often. They are
    #: the ones a user is most likely to search for, and they carry domains.
    priority: int = 0


@lru_cache(maxsize=1)
def _curated() -> dict[tuple[str, str], SeedToken]:
    raw = _seed_file("curated.json").read_text(encoding="utf-8")
    out: dict[tuple[str, str], SeedToken] = {}
    for row in json.loads(raw):
        provider = row["provider"]
        token = row["token"]
        out[(provider, token)] = SeedToken(
            provider=provider,
            token=token,
            company_name=row.get("name"),
            company_domain=row.get("domain"),
            priority=100,
        )
    return out


@lru_cache(maxsize=8)
def _bulk_tokens(provider: str) -> tuple[str, ...]:
    path = _seed_file(f"{provider}.txt")
    if not path.is_file():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(sorted({line.strip() for line in lines if line.strip()}))


def seed_tokens(providers: list[str] | None = None) -> list[SeedToken]:
    """Every seed token for the requested providers, curated entries merged in.

    Deduplicated on (provider, token): a curated company that also appears in the
    bulk list keeps its name, domain and priority.
    """
    wanted = list(providers) if providers else list(PROVIDER_NAMES)
    unknown = [p for p in wanted if p not in PROVIDER_NAMES]
    if unknown:
        raise ValueError(f"unknown providers: {', '.join(unknown)}")

    curated = _curated()
    seen: dict[tuple[str, str], SeedToken] = {}
    for provider in wanted:
        for token in _bulk_tokens(provider):
            key = (provider, token)
            seen[key] = curated.get(key, SeedToken(provider=provider, token=token))
    for key, entry in curated.items():
        if entry.provider in wanted:
            seen[key] = entry
    return sorted(seen.values(), key=lambda s: (-s.priority, s.provider, s.token))


def corpus_summary() -> dict[str, int]:
    """Token counts per provider, for the CLI and the docs to quote."""
    summary = {provider: len(_bulk_tokens(provider)) for provider in PROVIDER_NAMES}
    summary["curated"] = len(_curated())
    summary["total"] = len(seed_tokens())
    return summary
