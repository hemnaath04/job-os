"""The company-token corpus.

Token lists live in `seeds/*.txt` inside the package, one token per line. They
are bundled rather than read from a path outside the repo so the build and the
crawl do not depend on a developer's home directory.

    greenhouse.txt        8,333 tokens
    lever.txt             4,368 tokens
    ashby.txt             3,161 tokens
    smartrecruiters.txt       9 tokens
    workday.txt              13 tokens
    bamboohr.txt          4,992 tokens
    icims.txt                31 tokens
    oracle_cloud.txt         12 tokens
    curated.json             97 companies, with real employer names and domains

`bamboohr.txt` is present and is not crawled by default. See `HELD_PROVIDERS`
below for what that means, why it is a hold rather than a deletion, and the one
line that reverses it.

`workday.txt`, `icims.txt`, `oracle_cloud.txt` and `bamboohr.txt` are the
exceptions to the sentence below: every token in all four answered 200 with
jobs on 2026-08-30. None of them is guessable, and each is unguessable in its
own way.

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

BambooHR is unguessable in the most dangerous way of the four, because it does
not fail. A slug that does not exist redirects to the vendor's marketing site
and returns HTTP 200, and a lapsed account returns 200 from an expired-account
page. An unverified BambooHR token is therefore not a dead row, it is a row
that looks alive, so the provider proves a board by parsing the body rather
than by reading the status code. See `providers/bamboohr.py`.

The 4,992 here are also the one place where guessing was measured against a
better method rather than assumed to be the only one: guessing slugs returned
74 boards with postings from 838 candidates (8.8%), while slugs harvested from
the Common Crawl URL index returned 4,993 from 6,835 (73%). The conclusion in
the sentence below still holds; the corpus just did not have to rest on it.

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

`bamboohr.txt` is where the sentence above got tested properly, and the result
confirms it rather than contradicting it. Two discovery methods were run against
BambooHR on 2026-08-30 and every candidate from both was fetched:

  guessing         838 candidates ->   135 boards (16.1%) ->    74 with postings
  public indexes  6,835 candidates -> 6,254 boards (91.5%) -> 4,993 with postings

The guessing arm is the control, and it behaved exactly as the SmartRecruiters
note predicts: company-name guesses hit 2 times in 150, and short generic English
words did better (60 in 509) only because BambooHR's customers are small firms
that took the obvious subdomain. 8.8% end to end. Guessing is still not a
strategy.

What changed the outcome was having a real source, which is the thing that note
asks for:

  * **Common Crawl.** The CC-MAIN-2026-34 URL index, queried for the
    `bamboohr.com` domain and filtered to `[a-z0-9-]+\\.bamboohr\\.com/careers`,
    yielded 6,816 distinct slugs. These are career pages a crawler actually saw
    on the live web, which is why nine in ten of them resolve.
  * **GitHub code search** for `bamboohr.com/careers` across several languages
    yielded 82 distinct slugs. Small, but nearly all real.
  * **Certificate transparency (crt.sh)** was tried and produced nothing: the
    endpoint answered HTTP 502 throughout. Worth retrying another day, since a
    wildcard-free tenant subdomain should be visible there.

The 4,992 tokens seeded are the 4,993 boards that had at least one posting open,
minus `demo`, which is BambooHR's own demo tenant and not an employer. They held
37,301 live postings between them on the day they were checked, and the largest
single board (lanesgroup) held 197. A random 40-token subsample was re-fetched
afterwards and all 40 still answered with postings.

Every token in the file was confirmed by fetching it. None is a guess left in to
make the list look longer, and the ~1,261 slugs that were real boards with
nothing open were deliberately left out rather than counted as wins.
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


#: Providers a sweep does NOT crawl unless it is asked for them by name.
#:
#: This is a product decision pending evidence, not a judgement about the
#: provider or the quality of its seed file. `bamboohr.txt` is the most
#: carefully built list in this package: 4,992 tokens, every one confirmed by
#: fetching it, harvested from Common Crawl rather than guessed (see this
#: module's docstring for the measured 8.8%-versus-73% comparison). Nothing
#: about it is suspect.
#:
#: What is unproven is whether those boards are worth their share of the index.
#: They are small employers, they held 37,301 live postings between them, and
#: none of them has ever been shown to answer a search anybody ran. In an index
#: that has to fit a 500 MB storage budget (see
#: `db/models/job_posting.py::FTS_DESCRIPTION_CHARS`), a tenth of the rows for
#: unmeasured value is a real trade, and holding is the cheaper way to find
#: out than crawling first and pruning later.
#:
#: HELD, NOT DELETED, and the difference is the whole mechanism:
#:
#:   * `seeds/bamboohr.txt` stays in the package, unchanged.
#:   * `providers/bamboohr.py` stays registered in `PROVIDER_NAMES`.
#:   * `ingest sweep --providers bamboohr` crawls it, today, with no code
#:     change: naming a provider explicitly always wins over this list.
#:   * Rows already crawled from it stay in the index and stay searchable.
#:
#: To un-hold it, delete the string from this tuple. That is the entire
#: reversal, and it is the reason the mechanism is a name in a set rather than
#: a deleted file or a commented-out registry entry.
HELD_PROVIDERS: frozenset[str] = frozenset({"bamboohr"})

#: What a sweep crawls when nobody says otherwise. Order follows
#: `PROVIDER_NAMES` so the corpus summary and the sweep agree on it.
DEFAULT_CRAWL_PROVIDERS: tuple[str, ...] = tuple(
    p for p in PROVIDER_NAMES if p not in HELD_PROVIDERS
)


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

    `providers=None` means `DEFAULT_CRAWL_PROVIDERS`, which is every provider
    except the held ones. Naming a held provider explicitly still returns its
    tokens: the hold is a default, not a block.
    """
    wanted = list(providers) if providers else list(DEFAULT_CRAWL_PROVIDERS)
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
    """Token counts per provider, for the CLI and the docs to quote.

    Every provider is listed, held ones included, because the file is still
    there and a summary that hid it would make the hold look like a deletion.
    `total` counts only what a default sweep would actually crawl, and
    `held_total` is the difference, so the two numbers together say how much is
    being left on the table rather than leaving a reader to subtract.
    """
    summary = {provider: len(_bulk_tokens(provider)) for provider in PROVIDER_NAMES}
    summary["curated"] = len(_curated())
    summary["total"] = len(seed_tokens())
    summary["held_total"] = sum(len(_bulk_tokens(p)) for p in sorted(HELD_PROVIDERS))
    return summary
