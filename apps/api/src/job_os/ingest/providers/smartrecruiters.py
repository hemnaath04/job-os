"""SmartRecruiters job boards.

`GET api.smartrecruiters.com/v1/companies/{co}/postings?limit=&offset=` returns
`{"offset", "limit", "totalFound", "content": [...]}`.

**The trap.** A company that does not exist answers `200` with `totalFound: 0`,
byte-for-byte identical to a real company with nothing open. Measured on this
branch: `zzznotarealcompany9911` and `Square` both return 50 bytes and
`totalFound: 0`. So SmartRecruiters is the one provider where a token cannot be
proven dead from a single response, and `BoardStatus.EMPTY` is deliberately
returned for both. `liveness.py` resolves it over time: a token that has never
once returned a posting is retired after several EMPTY observations rather than
being pruned on the first one, because pruning on the first one would delete
every seasonal employer between hiring rounds.

**Pagination.** `limit` is clamped server-side to 100 (asking for 200 came back
`"limit": 100`), so a large board needs several calls. Measured: `BoardGroup`
reports 4,776 postings, which is 48 requests. `MAX_PAGES` caps that.

**No description in the list.** Listing rows carry only metadata; the JD body
needs a second call per posting to `/postings/{id}`, where it arrives as
`jobAd.sections.{companyDescription,jobDescription,qualifications,
additionalInformation}`, each `{title, text}`. At 48 pages plus one call per
posting, hydrating a board like that costs ~4,800 requests, so it is not done
during a sweep. Rows land with `jd_hydrated=False` and a `jd_clean` built from
the metadata the listing does provide, and `hydrate_descriptions` fills bodies in
for a bounded set later. That mirrors how the web app hydrates Greenhouse
descriptions only for postings it is about to show.
"""
from __future__ import annotations

from typing import Any

from job_os.ingest import normalize
from job_os.ingest.providers.base import BoardResult, BoardStatus, RawPosting, as_dict

NAME = "smartrecruiters"
HOST = "api.smartrecruiters.com"

PAGE_SIZE = 100
#: 100 pages is 10,000 postings, past which we are crawling a job board vendor's
#: whole enterprise tenant and should be told to do so explicitly.
MAX_PAGES = 100


class SmartRecruitersProvider:
    name = NAME
    host = HOST

    def board_url(self, token: str) -> str:
        return f"https://jobs.smartrecruiters.com/{token}"

    def api_url(self, token: str, offset: int = 0, limit: int = PAGE_SIZE) -> str:
        return (
            f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
            f"?limit={limit}&offset={offset}"
        )

    async def fetch_board(
        self,
        fetcher: Any,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        postings: list[RawPosting] = []
        bytes_fetched = 0
        requests_made = 0
        first_etag: str | None = None
        offset = 0

        for page in range(MAX_PAGES):
            # Only the first page is conditional. A 304 on page one means the
            # board is unchanged and the remaining pages can be skipped entirely.
            response = await fetcher.get_json(
                self.api_url(token, offset=offset),
                host=HOST,
                etag=etag if page == 0 else None,
                expect_bytes=expect_bytes if page == 0 else 0,
            )
            bytes_fetched += response.bytes_read
            requests_made += response.requests_made

            if response.not_modified:
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.NOT_MODIFIED,
                    http_status=304,
                    etag=etag,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                )
            if response.status_code == 404:
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.MISSING,
                    http_status=404,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                )
            if not response.ok:
                # A later page failing still leaves the earlier pages usable, but
                # the board list is now incomplete, so it must not be treated as
                # authoritative or the missing pages would be deactivated.
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.ERROR,
                    postings=postings,
                    http_status=response.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error=response.error or f"HTTP {response.status_code}",
                )

            payload = response.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.ERROR,
                    postings=postings,
                    http_status=200,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error="unexpected payload shape",
                )

            if page == 0:
                first_etag = response.etag

            content = payload["content"]
            for raw in content:
                parsed = parse_posting(token, raw)
                if parsed is not None:
                    postings.append(parsed)

            total_found = payload.get("totalFound")
            # The server clamps `limit`, so page forward by what it actually gave
            # rather than by what we asked for.
            served = payload.get("limit")
            step = served if isinstance(served, int) and served > 0 else PAGE_SIZE
            offset += step

            if not content:
                break
            if isinstance(total_found, int) and offset >= total_found:
                break

        return BoardResult(
            provider=NAME,
            token=token,
            # Deliberately EMPTY, not MISSING, when nothing came back: this
            # provider cannot distinguish an unknown company from an idle one.
            status=BoardStatus.LIVE if postings else BoardStatus.EMPTY,
            postings=postings,
            http_status=200,
            etag=first_etag,
            bytes_fetched=bytes_fetched,
            requests_made=requests_made,
        )


def parse_posting(token: str, raw: Any) -> RawPosting | None:
    if not isinstance(raw, dict):
        return None
    external_id = raw.get("id")
    title = (raw.get("name") or "").strip()
    if not external_id or not title:
        return None

    location = _location_label(raw.get("location"))
    country = _country_code(raw.get("location"))
    remote_flag = _remote_flag(raw.get("location"))
    company = as_dict(raw.get("company"))
    company_name = _text(company.get("name")) or token
    department = _label(raw.get("department"))

    # `ref` is an API URL, not something a person can open. The public posting
    # page follows a fixed shape from the company identifier and posting id.
    identifier = _text(company.get("identifier")) or token
    url = f"https://jobs.smartrecruiters.com/{identifier}/{external_id}"

    return RawPosting(
        source=NAME,
        board_token=token,
        external_id=str(external_id),
        title=title,
        company_name=company_name,
        source_url=url,
        # No body in the listing. Everything the listing does say goes in, so the
        # row is still rankable on more than its title until hydration runs.
        jd_clean=_metadata_body(title, company_name, location, raw),
        jd_hydrated=False,
        location=location,
        country_code=country,
        remote=normalize.is_remote(location, explicit=remote_flag),
        anywhere=normalize.is_anywhere(location),
        workplace_type=_workplace_type(raw.get("location")),
        employment_type=_label(raw.get("typeOfEmployment")),
        department=department,
        posted_at=normalize.to_datetime(raw.get("releasedDate")),
        posted_at_basis="published",
        extra={
            "uuid": raw.get("uuid"),
            "ref_number": _text(raw.get("refNumber")),
            "experience_level": _label(raw.get("experienceLevel")),
            "function": _label(raw.get("function")),
            "industry": _label(raw.get("industry")),
        },
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _label(value: object) -> str | None:
    """SmartRecruiters taxonomy fields are `{"id": ..., "label": ...}`."""
    if isinstance(value, dict):
        return _text(value.get("label")) or _text(value.get("id"))
    return _text(value)


def _location_label(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    full = _text(raw.get("fullLocation"))
    if full:
        return full
    parts = [_text(raw.get("city")), _text(raw.get("region")), _text(raw.get("country"))]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _country_code(raw: object) -> str | None:
    """The structured location carries a lowercase ISO-2 ("us")."""
    if not isinstance(raw, dict):
        return None
    code = _text(raw.get("country"))
    if code and len(code) == 2:
        return code.upper()
    return normalize.infer_country_code(_location_label(raw))


def _remote_flag(raw: object) -> bool | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("remote") is True:
        return True
    if raw.get("remote") is False and raw.get("hybrid") is not True:
        return False
    return None


def _workplace_type(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("remote") is True:
        return "Remote"
    if raw.get("hybrid") is True:
        return "Hybrid"
    return "Onsite" if raw.get("remote") is False else None


def _metadata_body(
    title: str, company: str, location: str | None, raw: dict[str, Any]
) -> str:
    """A searchable stand-in for the description we have not fetched yet.

    Everything in here came from the listing response, so it is factual, just
    thin. `jd_hydrated=False` on the row is what tells the read path not to
    present this as the job description.
    """
    fields = [
        title,
        company,
        location,
        _label(raw.get("department")),
        _label(raw.get("function")),
        _label(raw.get("industry")),
        _label(raw.get("experienceLevel")),
        _label(raw.get("typeOfEmployment")),
    ]
    return normalize.plain_text("\n".join(f for f in fields if f))


def sections_to_text(job_ad: object) -> str:
    """Flatten a detail response's `jobAd.sections` into plain text.

    Sections arrive as a dict of `{title, text}` in no guaranteed order, so the
    documented order is imposed here to keep the stored body stable across
    crawls; an unstable body would change the content hash and make every
    re-crawl look like an edit.
    """
    if not isinstance(job_ad, dict):
        return ""
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return ""
    order = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    keys = [k for k in order if k in sections] + sorted(
        k for k in sections if k not in order
    )
    chunks: list[str] = []
    for key in keys:
        section = sections[key]
        if not isinstance(section, dict):
            continue
        text = normalize.html_to_text(section.get("text"))
        if not text:
            continue
        heading = _text(section.get("title"))
        chunks.append(f"{heading}\n{text}" if heading else text)
    return normalize.plain_text("\n\n".join(chunks))


def detail_url(token: str, external_id: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{external_id}"
