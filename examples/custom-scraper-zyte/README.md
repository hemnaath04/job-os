# custom-scraper-zyte

A ready-made **custom source** for job.os, backed by the [Zyte API](https://www.zyte.com/zyte-api/).

You deploy this to your own Vercel account, point it at the job listing pages
you care about, and paste the deployment URL into job.os under **Custom
sources**. job.os then POSTs its search filters here on every search and renders
whatever this returns. job.os never touches the job sites itself; this function
does, on your infrastructure and with your Zyte key.

It has no dependencies: one serverless function, Node built-ins, global `fetch`.
There is nothing to build and nothing to keep up to date.

---

## 1. Get a Zyte API key

1. Create an account at [zyte.com](https://www.zyte.com/) and open Zyte API.
2. Copy your API key from the API access page.
3. New accounts get **$5 of free trial credit**. After that Zyte is pay as you
   go. **All costs land on your own Zyte account**, not on job.os.

Extraction is billed per request, so two knobs matter: how many pages you list
in `TARGET_URLS`, and `ZYTE_HYDRATE_LIMIT` (each hydrated posting is a second
request). Start with one target and hydration off.

## 2. Deploy to Vercel

```bash
npm i -g vercel
cd examples/custom-scraper-zyte
vercel deploy
```

Accept the defaults. Vercel picks up `api/jobs.js` as a serverless function, so
there is no framework to choose and no build command to set.

## 3. Set the environment variables

In the Vercel dashboard, open the project, then Settings, then Environment
Variables. See `.env.example` for the full descriptions.

| Variable | Required | What it does |
| --- | --- | --- |
| `ZYTE_API_KEY` | yes | Your Zyte API key. |
| `TARGET_URLS` | yes | Comma-separated job listing pages to scrape. |
| `ZYTE_HYDRATE_LIMIT` | no | Open this many postings for company, location, date and description. Default `0`. |
| `SHARED_SECRET` | no | When set, requests must carry `x-custom-source-key` with this value or get a 401. |

Redeploy after adding them (`vercel deploy --prod`), because environment
variables are read at request time on the deployment that has them.

## 4. Connect it to job.os

1. Open job.os, go to **Job finder**, then **Connect job sources**, then the
   **Custom sources** section.
2. Read and accept the terms. Nothing can be added before that.
3. Add a source:
   - **Name**: anything, for example `My Zyte scraper`.
   - **Endpoint URL**: your deployment URL plus `/api/jobs`, for example
     `https://custom-scraper-zyte.vercel.app/api/jobs`.
   - If you set `SHARED_SECRET`, set **Auth header name** to
     `x-custom-source-key` and **Auth header value** to that secret.
4. Save, then switch the source on in the Job finder sources list.

## 5. Test it

A `GET` is accepted so you can open the URL in a browser. From the command line:

```bash
curl -s -X POST https://YOUR-DEPLOYMENT.vercel.app/api/jobs \
  -H 'content-type: application/json' \
  -H 'x-custom-source-key: YOUR_SHARED_SECRET' \
  -d '{"title_keywords":["software engineer"],"location":null,"country_codes":["US"],"max_age_days":30,"limit":5}'
```

Locally, with `vercel dev` and a `.env` copied from `.env.example`:

```bash
vercel dev
curl -s -X POST http://localhost:3000/api/jobs \
  -H 'content-type: application/json' -d '{"limit":5}'
```

## The contract

Request body from job.os:

```json
{
  "title_keywords": ["software engineer"],
  "location": "Boston",
  "country_codes": ["US"],
  "max_age_days": 30,
  "limit": 20
}
```

Response, either a bare array or `{ "results": [...] }`. Only `title` and `url`
are required:

```json
{
  "results": [
    {
      "title": "Software Engineer",
      "url": "https://example.com/jobs/123",
      "company": "Example Inc",
      "company_domain": "example.com",
      "location": "Boston, MA",
      "country_code": "US",
      "posted_at": "2026-07-01T00:00:00Z",
      "description": "...",
      "id": "123"
    }
  ]
}
```

job.os re-applies its own filters to whatever comes back, so returning a whole
board and letting job.os narrow it is fine. Responses over about 2 MB, or slower
than 15 seconds, are dropped.

## How it works

For each URL in `TARGET_URLS` the function calls Zyte with
`{"url": target, "jobPostingNavigation": true}` and reads `items` off the
response: each entry is a link to one posting, with the link text in `name`.
Those become the `title` and `url` of a result, deduplicated by URL and capped
at the `limit` job.os asked for.

If `ZYTE_HYDRATE_LIMIT` is above zero, that many results are then fetched
individually with `{"url": job url, "jobPosting": true}` to fill in `company`
(from `hiringOrganization.name`), `location` (from `jobLocation.raw`),
`posted_at` (from `datePosted`) and `description`. Hydration is best effort: a
posting that fails to hydrate keeps the title and URL it already had.

Neither the Zyte key nor the shared secret is ever logged.

## LEGAL

**You are responsible for the sites you put in `TARGET_URLS` and for complying
with their terms of service, robots rules, rate limits, intellectual property
rights, and all applicable law. This template is provided as is, with no
warranty of any kind. It is not legal advice. If you are not sure whether a site
permits this, do not add it.**
