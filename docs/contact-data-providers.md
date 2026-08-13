# Contact data providers: decision memo

Written 2026-08-12. Every price below was checked against the web on that date and
every claim carries a source. Contact-data pricing changes often, so treat
anything here as stale after roughly a quarter and re-check the links marked
AUTHORITATIVE before spending money.

Re-verified 2026-08-12 against the vendors' own pages rather than review sites:
hunter.io/pricing, hunter.io/api-documentation, rocketreach.co/pricing,
apollo.io/pricing, apollo.io/terms/api and the Proxycurl shutdown post. Two
numbers taken from third-party reviews were wrong and are corrected below, both
in RocketReach's and Apollo's sections. Where a vendor's own page could not be
read (its comparison table renders client side), the claim is labelled as
third-party reported.

## The question this memo answers

The outreach feature has two halves and they have very different risk profiles.

1. **Drafting.** Given a job, a target person's role, and the user's verified
   fact vault, write a short outreach message. Built on this branch. No third
   party involved.
2. **Discovery.** Find out that Priya Raman is an engineering manager on the
   payments team at Stripe, and find her work email. Not built. This memo decides
   what, if anything, we buy for it later.

Discovery is the half with legal and ethical exposure, so the code ships with a
provider interface and a manual implementation (the user pastes what they found
themselves) and nothing else. See `apps/api/src/job_os/services/contact_providers.py`.

## What is explicitly not on the table

Scraping LinkedIn profiles. LinkedIn's User Agreement section 8.2 prohibits
developing, supporting or using software, scripts, bots, crawlers or browser
extensions to scrape the service or copy profiles and other data from it, and
prohibits using automated methods to add or download contacts. That language has
not loosened.
([LinkedIn prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions?lang=en),
[section 8.2 summary](https://connectsafely.ai/articles/is-linkedin-automation-safe-tos-scraping-guide-2026))

This is not a theoretical risk. In January 2025 LinkedIn and Microsoft sued
Nubela, the company behind Proxycurl, alleging it created hundreds of thousands
of fake accounts to scrape millions of profiles including non-public data and
resold that data through an API. Proxycurl settled and shut down on 4 July 2025.
Its founder's own shutdown post puts the business at roughly 10 million dollars
of revenue at the time, so this was not a marginal vendor folding. The API no
longer answers and sign-ups are closed.
([Proxycurl shutdown notice](https://nubela.co/blog/goodbye-proxycurl/),
[founder's retrospective](https://nubela.co/blog/is-scraping-linkedin-legal-in-2026/),
[coverage](https://www.startuphub.ai/ai-news/startup-news/2025/the-1-linkedin-scraping-startup-proxycurl-shuts-down))

The takeaway for us is narrower than "scraping is illegal". It is that a
LinkedIn-derived contact API is a dependency that can be switched off by
litigation with no notice, taking the feature with it. Any provider we pick has
to source from the open web.

## The providers

### Proxycurl: dead, remove from consideration

Shut down 4 July 2025 after the LinkedIn suit. Nothing to price. Listed here only
because the task named it and because its old docs still rank in search results,
which makes it easy to build against by accident.
([source](https://nubela.co/blog/goodbye-proxycurl/))

### Hunter.io: the recommendation

| | |
|---|---|
| Free tier | 50 credits per month, and the API is available on the free plan |
| Paid | Starter 49 USD per month, 34 USD per month billed annually, 2,000 credits per month. Growth 149 USD, 104 USD annual, 10,000 credits. Scale 299 USD, 209 USD annual, 25,000 credits |
| Credit maths | 1 credit per email found via Email Finder, Domain Search or Bulk Email Finder. 1 credit per 10 emails via Bulk Domain Search. 0.5 credit per email verified. Counted identically whether the call comes from the web app, the API or the extension |
| API | Available on all plans including free. Documented endpoints: Domain Search, Email Finder, Email Verifier, Email Count, Discover, and the enrichment endpoints. Bearer token, `X-API-KEY` header, or `api_key` query param. 15 requests per second and 500 per minute on Email Finder and Domain Search |
| Accuracy | Confidence score 0 to 100 per result. Roughly 95 means the address was found in a public source, roughly 60 means the address was inferred from the domain's observed pattern. Independent tests report about 91 percent valid on standard B2B corporate domains with 1.2 percent hard bounce and 5.4 percent catch-all; results Hunter marks Verified test at about 97 percent deliverable |
| Terms posture | Business or professional use only. Prohibits building a similar or competitive service. Users remain independent data controllers under GDPR. Signable DPA. Data is collected from public web sources, not from private inboxes or personal social profiles |

Sources: [Hunter pricing and the credit FAQ on it, AUTHORITATIVE](https://hunter.io/pricing),
[Hunter API documentation, AUTHORITATIVE](https://hunter.io/api-documentation),
[Hunter terms of service](https://www.hunter.io/terms-of-service),
[Hunter GDPR help article](https://help.hunter.io/en/articles/1890029-gdpr-compliance),
[confidence score explainer](https://help.hunter.io/using-hunter/email-verifier-and-confidence-score),
[accuracy testing](https://crosstechos.com/hunter-io-email-finder/),
[second accuracy review](https://growthhacksuite.com/hunter-io-email-finder).

**How the free-API claim was checked**, since the whole recommendation rests on
it. Hunter now sells a separate "Data Platform" product described as API-only
access, which reads at a glance like the API moved behind a paid tier. It did
not. The API page still offers "Get my free API key" on sign-up, and the API
reference documents Free-plan behaviour inline on the ordinary endpoints: Domain
Search returns `pagination_error` when a Free-plan user asks for more than 10
results, and the account endpoint returns only the requesting user's own accounts
on Free. Both of those are descriptions of a Free-plan account calling the API.
The Data Platform is a volume product on top, not a gate.

**Pricing discrepancy worth knowing.** Hunter's own pricing page states 50 credits
per month on Free. Several 2026 review sites still describe the free tier as "25
searches and 50 verifications per month", which was the older shape.
([example](https://derrick-app.com/tools/hunter-pricing)) Trust the pricing page,
and treat the free allowance as "tens of lookups per month, not hundreds".

**Why it wins.** Three reasons, in order of weight.

1. It is the only provider on this list whose API works on a free plan. Everything
   else gates the API behind a tier that costs more per month than the whole rest
   of this project's infrastructure.
2. Domain Search solves the discovery half, not just the email half. Give it
   `stripe.com` and it returns names, titles and addresses that Hunter found on
   public web pages. That is the Insider Connections mechanic without touching
   LinkedIn.
3. Its data provenance is the one we can defend out loud. Public company pages,
   press releases and professional directories, never a private inbox or a social
   profile. If the user is ever asked where an address came from, that answer is
   survivable.

**Where it does not fit.** The terms permit business and professional use, and the
India-specific clause says paid plans are for businesses and that consumers should
not attempt to purchase. A personal job search by a student is professional use of
a business email address, which reads as in-bounds, but it is not the use the terms
were drafted for. The free tier avoids the question entirely, since there is no
purchase. Also: 50 credits per month is genuinely tight. It is roughly 12 target
companies a month at 4 lookups each, which happens to be about the right volume for
a considered job search and completely wrong for a spray.

### Prospeo: the cheap second hop

75 verified emails per month free with no credit card and no expiry, plus 100
Chrome extension credits, and pay as you go from 0.01 USD per verified contact.
([source](https://prospeo.io/s/free-business-email-search),
[pricing overview](https://prospeo.io/s/findymail-pricing))

Worth having as a fallback for the case Hunter returns nothing, because a second
source on a different index genuinely lifts hit rate and because 0.01 USD per
contact means a bad month costs a coffee. Not the primary: less documented API
surface, and the free-tier numbers come from the vendor's own comparison pages
rather than a clean pricing page.

### AnyMail Finder, LeadMagic, Findymail: viable, unnecessary

- AnyMail Finder: 75 emails per month free, no credit card, pay for results.
- LeadMagic: 2,000 credits for 49.99 USD per month, about 0.025 USD per credit.
- Findymail: 49 USD per month for 1,000 credits, about 0.049 USD per credit,
  free trial of 10 finder credits.

([comparison source](https://leadmagic.io/comparisons/leadmagic-vs-findymail),
[Findymail pricing](https://prospeo.io/s/findymail-pricing),
[AnyMail review](https://prospeo.io/s/anymail-finder-extension))

All three are competent and all three are priced for a sales team sending
thousands of emails a month. A single job seeker sending 30 messages a month has
no use for 1,000 credits. Note that the citations above are vendor-on-vendor
comparison pages, which have an obvious bias; verify on the vendor's own pricing
page before buying.

### Apollo.io: no

| | |
|---|---|
| Free tier | 900 credits per YEAR, which is the number on Apollo's own pricing page today. Roughly 75 a month, and one email reveal costs one credit. No API access |
| Paid | Basic 49 USD per user per month billed annually, 59 monthly. Professional 79 annual, 99 monthly. Organization 119 annual, 149 monthly, which is the first tier with the API |
| Terms | Non-exclusive licence for internal business purposes only. May not sublicense, sell or distribute. May not integrate the API with your product or service without Apollo's authorisation. May not use the API to replicate or compete with Apollo. May not let a third party use Apollo data to train or improve an AI or ML model |

Sources: [Apollo API terms, AUTHORITATIVE](https://www.apollo.io/terms/api),
[Apollo pricing and credits, AUTHORITATIVE](https://www.apollo.io/pricing),
[plan and credit breakdown](https://salesmotion.io/blog/apollo-pricing).

**Correction, 2026-08-12.** An earlier draft of this memo said the free tier was
"100 email credits per month, down from 10,000", taken from a review site.
Apollo's own pricing page states 900 credits per year on Free. The correction
makes the free tier slightly worse than the memo first claimed, not better, so
the conclusion does not move. That the API tier and the integration clause are
what disqualify Apollo, and not the credit count, is the reason this correction
is a footnote rather than a rewrite. That the third-party number was wrong at all
is the reason every remaining number here was re-checked against a vendor page.

Three independent disqualifications, any one of which is enough.

1. The API starts at the Organization tier. Roughly 1,400 USD a year to look up
   maybe 40 people a month.
2. "May not integrate the APIs with your product or services unless Apollo has
   authorized or approved such access or integration" is a direct problem for a
   product that integrates a contact API. Even for a personal tool, it is the
   clause you would have to get written permission against.
3. Apollo was named in LinkedIn's 2025 enforcement wave alongside Seamless.ai,
   which puts part of its index in the same category of risk that took Proxycurl
   down. ([source](https://www.leadgenius.com/resources/linkedins-crackdown-on-data-scrapers-why-apollo-io-and-seamless-ai-were-targeted--and-whos-next))

### Clearbit, now HubSpot Breeze Intelligence: no

Clearbit was acquired by HubSpot in November 2023 and rebranded Breeze
Intelligence. There is no standalone product and no free tier. Credits run
roughly 45 USD per month for 100 on top of a paid HubSpot subscription from
20 USD per month, so the realistic floor is about 65 USD per month. Standard
enrichment became free on Core Seats after INBOUND 2025, which only matters if
you are already a HubSpot customer. No phone enrichment. The strength was always
company-level firmographics rather than finding a named individual's address.

Sources: [Breeze pricing breakdown](https://marketbetter.ai/blog/clearbit-pricing-breakdown-2026/),
[post-acquisition review](https://marketbetter.ai/blog/clearbit-review-2026/),
[Clearbit API after the sunset](https://abmatic.ai/blog/clearbit-enrichment-api-2026).

Disqualified by the HubSpot dependency alone. We are not adding a CRM to send an
email to a hiring manager.

### RocketReach: no

Read off RocketReach's own pricing page: Essentials 33 USD per month, 399 USD
billed annually, 1,200 exports per year. Pro 75 USD per month, 899 USD billed
annually, 3,600 exports. Ultimate 142 USD per month, 1,699 USD billed annually,
20,000 exports. Five free lookups on sign-up with no card, and no permanent free
tier. Custom team plans start at 6,000 USD annually.

**The API is Ultimate only.** RocketReach's plan comparison table has an API
Access row, but the ticks in that table render client side and could not be read
from the page source, so the tier mapping here is third-party reported rather
than read off the vendor: two independent reviews put API access on Ultimate and
nowhere below it. Treat the tier as confirmed and the exact entitlement as worth
one email to their sales team before anyone pays.

Sources: [RocketReach pricing, AUTHORITATIVE](https://rocketreach.co/pricing),
[API access is Ultimate only](https://www.cleanlist.ai/blog/2026-03-19-rocketreach-pricing-guide),
[same claim, second source](https://salesmotion.io/blog/rocketreach-pricing),
[API rate limits](https://docs.rocketreach.co/reference/rate-limits).

**Correction, 2026-08-12.** An earlier draft priced Ultimate at 207 USD per
month and 2,099 USD per year, and Pro at 83 USD per month, from review sites.
RocketReach's own page says 142 and 1,699, and 75. The real figures are cheaper
than the memo claimed and the answer is still no: 1,699 USD a year to look up
perhaps 40 people a month is two orders of magnitude past what this is worth,
and the free tier is five lookups in total rather than five a month. Cheaper and
still disqualified is the honest version of that sentence.

Disqualified on cost. 1,699 USD per year for API access is not defensible for a
personal job search.

### People Data Labs: no, and the free tier is a trap

Free plan gives 100 person or company lookups per month plus 25 IP lookups, always
free. The catch is that on the free plan **contact fields including email and
phone are returned as true or false flags rather than as values**, so the free tier
tells you an address exists and never tells you what it is. Pro is 98 USD per month
for 350 person enrichment credits and 1,000 company lookups, about 0.28 USD per
credit, sliding to 0.25 USD at higher volume.

Sources: [PDL pricing analysis](https://fullenrich.com/content/people-data-labs-pricing),
[per-credit tiers](https://nubela.co/blog/people-data-labs-pricing/),
[review](https://syncgtm.com/blog/people-data-labs-review).

PDL is a good product aimed at a different job: bulk enrichment of a list you
already have, at dataset scale. It does not answer "who should I talk to at this
company", and its free tier cannot answer "what is this person's address".

### Firecrawl: already paid for, and the first thing to try

Not a contact-data provider, and that is the point. `FIRECRAWL_API_KEY` is
already in this repo's environment and
`apps/api/src/job_os/integrations/firecrawl.py` already wraps it. A company's own
`/team`, `/about` or `/leadership` page is public, first-party, current, and often
carries the name and sometimes the address of the person the user wants. The
company published it deliberately.

Zero new vendors, zero new terms to accept, no third party in the loop, and the
provenance is unimpeachable because the source URL is the company's own page. It
will not work on a 5,000-person company that publishes no team page, which is
exactly the case where Hunter's Domain Search earns its credit.

### GitHub Student Developer Pack: nothing relevant

Checked. The pack carries GitHub Pro, Copilot, JetBrains, DigitalOcean credit,
Azure for Students, MongoDB Atlas credit, DataCamp, GitKraken, a free domain,
Testmail, Requestly and several dozen smaller developer offers. There is no
contact-data, email-finding or B2B-enrichment offer in it.
([offer list](https://saastweaks.com/startup-credits/github-student-developer-pack),
[official page](https://github.com/education/students))

Note in passing, unrelated to contacts: GitHub paused new sign-ups for the free
Copilot Student plan in April 2026.
([source](https://perkstack.co/blog/github-student-pack-guide))

## Recommendation

**Ship nothing. Wire the interface. When discovery gets built, build it as a
three-step waterfall and stop at the first hit.**

1. **Firecrawl on the company's own team page.** Already paid for, already wrapped,
   best provenance, zero marginal cost. Stop here when it works.
2. **Hunter.io Domain Search and Email Finder on the free tier.** 50 credits per
   month, API on the free plan, public-web sourcing, a confidence score we can show
   the user, and a DPA if it is ever needed. Upgrade to Starter at 34 USD per month
   annually only if 50 credits per month turns out to bind, which for a considered
   job search it probably will not.
3. **Prospeo pay as you go at 0.01 USD per contact** as the miss handler, capped by
   a hard monthly spend limit in our own code.

Rejected: Proxycurl (dead), Apollo (API tier cost, integration clause, LinkedIn
exposure), Clearbit and Breeze (HubSpot dependency, company-level focus),
RocketReach (1,699 USD per year for API), People Data Labs (free tier returns
flags not addresses, wrong job shape).

Three product rules that go with the recommendation, and these matter more than
the vendor choice:

- **Show the confidence score to the user, never round it away.** A 60 means Hunter
  guessed from a domain pattern. Sending to a guessed address and having it bounce
  is a cost the user should get to decide about, and the drafting code already
  distinguishes an address the user typed from one a provider inferred.
- **Store the source URL with every contact.** It is what makes an address
  defensible later and it is free to keep.
- **Honour deletion requests locally.** Hunter processes removal requests from
  individuals; if a person asks the user to stop, that has to be recordable on our
  row too, not just upstream.

## What the code assumes

`ContactProvider` in `apps/api/src/job_os/services/contact_providers.py` is a
Protocol with one method, `find(query) -> list[ContactCandidate]`. Every candidate
carries `source`, `confidence` and `evidence_url`, so a provider that infers an
address cannot present it as one that was read off a page. `ManualContactProvider`
is the only registered implementation: it returns no search results, because the
user does the searching, and it normalises and validates what they paste.

Adding Hunter later means one new class in that module, one entry in `_PROVIDERS`,
and no change anywhere else. That is the whole reason the interface exists before
any provider does.
