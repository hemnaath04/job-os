# custom-scraper-zyte

A ready-made **custom source** for job.os: a set of site spiders behind one
endpoint, powered by the [Zyte API](https://www.zyte.com/zyte-api/).

You deploy this to your own Vercel account and paste the deployment URL into
job.os under **Custom sources**. job.os then POSTs its search filters here on
every search and renders whatever comes back. job.os never touches the job sites
itself; this function does, on your infrastructure and with your Zyte key.

No dependencies: a few small ES modules, Node built-ins and global `fetch`.
Nothing to build, nothing to keep up to date.

---

## The spiders

| Spider | Default | State |
| --- | --- | --- |
| `linkedin` | on | Works. The most reliable of the set. |
| `indeed` | on | Works, but brittle. |
| `google` | on | Works, but the most volatile. |
| `handshake` | **off** | Experimental, and inert unless you supply your own session. |

**`linkedin`** calls the public `jobs-guest` endpoint, which is what the
logged-out job search page calls as you scroll. No login and no cookie. It
answers with a static HTML fragment, so it uses a plain Zyte HTTP fetch rather
than browser rendering, which is cheaper and faster. Zyte is still in the path
because a bare request from a single IP gets rate limited within a few pages.
This markup has been stable for years.

**`indeed`** goes through Zyte browser rendering, because Indeed runs serious
anti-bot protection. The data is read out of a JSON blob the page embeds for its
own React app (`window.mosaic.providerData["mosaic-provider-jobcards"]`) rather
than out of the markup. That blob is an internal contract, so Indeed can rename
it at any time and this spider goes quiet. It reports an error rather than
crashing when that happens.

**`google`** queries Google's jobs vertical (`udm=8`). It is the most volatile
spider here: Google obfuscates its class names, changes them often, and lazy
loads cards on scroll. So the primary path is not a selector at all, it asks
Zyte for `jobPostingNavigation`, which is ML extraction and does not care what
the class names are this week. A selector parse (with a Zyte `scrollBottom`
action) runs only as a fallback when extraction returns nothing, and that is the
part you should expect to have to update.

> If you want Google-for-Jobs coverage without maintaining any of this, job.os
> already ships **JSearch** as a bring-your-own-key source. Same data, stable
> API, free tier, no scraping. Connect it on the same page under "Add a key for
> more coverage" and leave `google` out of `SPIDERS`. The spider is for people
> who specifically want it running on their own infrastructure.

**`handshake`** is experimental and off by default. Read this before enabling
it:

- Handshake has no public job feed. Everything requires an authenticated
  session tied to your school account. There is no key to buy.
- So the spider cannot work on its own. It runs only if you set **both**
  `HANDSHAKE_HOST` and `HANDSHAKE_SESSION_COOKIE`. Without both it returns
  nothing and is completely inert.
- That cookie expires often, typically within days. When it does the spider
  quietly returns nothing until you paste a fresh one. Treat it as a manual,
  occasional tool.
- The request goes out through plain `fetch` rather than Zyte, because it
  carries your session and your session should not be handed to a third-party
  proxy.
- Handshake's internal endpoints are not a public API. The parsing is defensive
  guesswork.
- Using your own credentials to pull data is on you. Your school's and
  Handshake's terms apply.

Adding a site means writing a module in `spiders/` that exports `name` and
`async run(params, deps)`, then listing it in `spiders/index.js`.

## 1. Get a Zyte API key

1. Create an account at [zyte.com](https://www.zyte.com/) and open Zyte API.
2. Copy your API key from the API access page.
3. All costs land on your own Zyte account, not on job.os.

Browser rendering (Indeed, Google) costs more per request than a plain fetch
(LinkedIn). Trimming `SPIDERS` to the sites you actually want is the main lever
on spend.

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
| `SPIDERS` | no | Comma-separated spiders to run. Default `linkedin,indeed,google`. |
| `SHARED_SECRET` | recommended | When set, requests must carry `x-custom-source-key` with this value or get a 401. Without it, anyone who finds your URL can spend your Zyte credit. |
| `HANDSHAKE_HOST` | handshake only | For example `app.joinhandshake.com`. |
| `HANDSHAKE_SESSION_COOKIE` | handshake only | Your own logged-in Cookie header value. |

Redeploy after adding them (`vercel deploy --prod`): environment variables are
read at request time on the deployment that has them.

## 4. Connect it to job.os

1. Open job.os, go to **Job finder**, then **Connect job sources**, then the
   **Custom sources** section.
2. Read and accept the terms. Nothing can be added before that.
3. Add a source:
   - **Name**: anything, for example `My spiders`.
   - **Endpoint URL**: your deployment URL plus `/api/jobs`, for example
     `https://custom-scraper-zyte.vercel.app/api/jobs`.
   - If you set `SHARED_SECRET`, set **Auth header name** to
     `x-custom-source-key` and **Auth header value** to that secret.
4. Save, then switch the source on in the Job finder sources list.

## 5. Test it

```bash
curl -s -X POST https://YOUR-DEPLOYMENT.vercel.app/api/jobs \
  -H 'content-type: application/json' \
  -H 'x-custom-source-key: YOUR_SHARED_SECRET' \
  -d '{"title_keywords":["software engineer"],"location":"Boston","country_codes":["US"],"max_age_days":30,"limit":5}'
```

A `GET` is accepted too, so you can open the URL in a browser to check the
deployment is alive.

Locally, with `vercel dev` and a `.env` copied from `.env.example`:

```bash
vercel dev
curl -s -X POST http://localhost:3000/api/jobs \
  -H 'content-type: application/json' -d '{"title_keywords":["data engineer"],"limit":5}'
```

## The contract

Request body from job.os:

```json
{
  "title_keywords": ["software engineer"],
  "location": "Boston",
  "country_codes": ["US"],
  "max_age_days": 30,
  "limit": 25
}
```

Response. Only `title` and `url` are required on a result; everything else may
be null. The `errors` array is extra diagnostic information for you, and job.os
ignores it:

```json
{
  "results": [
    {
      "title": "Software Engineer",
      "url": "https://example.com/jobs/123",
      "company": "Example Inc",
      "company_domain": null,
      "location": "Boston, MA",
      "country_code": null,
      "posted_at": "2026-07-01T00:00:00Z",
      "description": null,
      "id": "123"
    }
  ],
  "errors": ["indeed: no job cards found: Indeed changed its page shape, or the request was blocked"]
}
```

job.os re-applies its own filters (title keywords, location, country, age) to
whatever comes back, so returning more than was asked for and letting job.os
narrow it is fine. Responses over about 2 MB, or slower than 15 seconds, are
dropped by job.os.

A spider that fails does not sink the response: its error is collected, the
other spiders still return their rows, and the status stays 200. Only every
spider failing produces a 502.

## Layout

```
api/jobs.js        the serverless function: auth, params, fan-out, dedupe, cap
zyte.js            zyteFetch (page HTML) and zyteExtract (ML extraction)
html.js            entity decoding, tag stripping, embedded-JSON extraction
spiders/index.js   the registry and the default spider list
spiders/*.js       one module per site
```

Neither the Zyte key nor the shared secret nor the Handshake cookie is ever
logged.

## LEGAL

**You are solely responsible for the sites these spiders visit and for
complying with their terms of service, robots rules, rate limits, intellectual
property rights, and all applicable law. Scraping some of these sites may
violate their terms. This template is provided as is, with no warranty of any
kind. It is not legal advice. If you are not sure whether a site permits this,
do not enable that spider.**
