# Job alerts and email digests

Standing alerts on saved searches. A scheduled run finds what is new for each
subscription, drops anything that user has already been mailed, and sends one
digest. It ships switched off.

Everything here is on the `feat/job-alerts` branch. Nothing about it is enabled
in any deployed environment yet; the last section is the checklist for that.

## Why Resend

The repo had no email capability at all before this, so the provider was an open
choice rather than something inherited.

**The GitHub Student Developer Pack was checked first**, since the account has
one and a free credit beats a free tier. It does not help here:

- **Mailgun** was added to the pack in July 2020 and pulled on 10 March 2025.
  The changelog entry reads "Mailgun is experiencing technical issues causing
  their offer to be paused and unpublished from the Student Developer Pack", and
  Sinch has since said on the GitHub community forum that it terminated the
  student plan as a service offering over abuse. Not available.
- **SendGrid** is still listed in the pack, but Twilio retired the free Email API
  and Marketing Campaigns plans effective **27 May 2025**, after a 60 day
  transition, with email sending paused for accounts on all free plans. New
  direct signups get a 60 day trial rather than a standing free tier. A trial
  that expires is not something to build a scheduled sender on.

That leaves the open market. **Resend** is the pick:

| | Free tier, verified 12 August 2026 |
|---|---|
| Emails per month | 3,000 |
| Emails per day | 100 |
| Verified domains | 1 |
| Log retention | 30 days |

Taken from Resend's own pricing page, not a roundup. The daily cap is the binding
one: one daily digest per subscription means the free tier tops out at about 100
active daily subscriptions, and a weekly cadence stretches that considerably
further. That is comfortably beyond where this is now and is a number worth
re-checking before any real launch.

Two other things made it the right shape. It is HTTP only with no SMTP relay to
configure, which is why `integrations/email.py` is an HTTP client rather than an
`smtplib` wrapper. And swapping it out later is one class and one settings value,
because nothing above `EmailTransport` knows which provider is in use.

Sources:

- [Resend pricing](https://resend.com/pricing)
- [Student Developer Pack changelog](https://github.com/github-education-resources/Student-Developer-Pack-Current-Partners-FAQ/blob/main/SDP-changelog.md)
- [Mailgun stops offering student-pack offer](https://github.com/orgs/community/discussions/153188)
- [Changes coming to SendGrid's Free Plan](https://www.twilio.com/en-us/changelog/sendgrid-free-plan)

Pricing goes stale. Re-verify before relying on any number above.

## Legal requirements this implements

These are commercial email under CAN-SPAM, so the following are requirements and
not preferences.

**The opt-out cannot sit behind a login.** 16 CFR 316.5: "Neither a sender nor
any person acting on behalf of a sender may require that any recipient pay any
fee, provide any information other than the recipient's electronic mail address
and opt-out preferences, or take any other steps except sending a reply
electronic mail message or visiting a single Internet Web page" in order to opt
out or have that opt-out honoured. A sign-in wall is more than a single web page,
so the unsubscribe link authorises itself with an HMAC token instead of a
session. That is why `ALERT_LINK_BASE_URL` points at the API origin and not at
the Next.js app, whose routes are all behind Clerk.

**The link has to keep working for at least 30 days** after the message goes out
(15 U.S.C. 7704(a)(3)). Tokens are signed rather than stored and carry no expiry,
so they keep working until the secret is rotated.

**An opt-out has to be honoured within 10 business days** (15 U.S.C. 7704(a)(4)).
It is honoured synchronously, on the request.

**Every message needs a valid physical postal address** (15 U.S.C. 7704(a)(5)).
`ALERT_POSTAL_ADDRESS`, with no default, and the run refuses to send without it.

Separately, RFC 8058 one-click: `List-Unsubscribe` carries the HTTPS URI and
`List-Unsubscribe-Post: List-Unsubscribe=One-Click` tells the client it may POST
without asking the user to confirm. Gmail and Yahoo then show a native
unsubscribe control, which is a better opt-out than any link in a body. RFC 8058
also requires DKIM to cover both headers; that is sending-domain configuration
and is on the checklist below.

Sources:

- [16 CFR 316.5](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-316/section-316.5)
- [15 U.S.C. 7704](https://www.law.cornell.edu/uscode/text/15/7704)
- [FTC CAN-SPAM compliance guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)

## Honest freshness

The most common complaint about the market leader is jobs shown as posted an hour
ago that have been on the market for weeks. That happens because boards reset the
posting date on every repost and aggregators show whatever the board currently
says. The number is real. It is just not the number the reader thinks it is.

`services/alert_freshness.py` refuses that trade. Two dates go in: `posted_at`,
which is what the source claims and is never treated as fact, and
`first_seen_at`, which is the earliest record we have of the role from our own
sent log or jobs table. The label always says which one it used, and every
source-supplied date is marked estimated. When the source claims a date more than
three days newer than our own first sighting, the label leads with our date and
says plainly that the source is showing a repost date. The threshold is three
days rather than zero because crawl order and board-side timezones move a date by
hours, and flagging that would put a warning on nearly every listing.

Rows sort by the age the label actually claims, so the order agrees with the
words.

## Never sending the same job twice

`alert_sends` is one row per job per user, with two keys:

- `source_key`, `"{source}:{source_id}"`, catches the same listing seen again.
- `content_key`, a hash of normalised company, title and location, catches the
  same role reposted under a new id or found through a second source.

A unique constraint on `(user_id, source_key)` makes this a property of the
database rather than of a code path. It is scoped to the user rather than to the
subscription on purpose: two overlapping saved searches must not both mail the
same role.

The honest limit: a repost that rewrites "Sr. Engineer (Remote)" as "Senior
Engineer, Remote" still gets through the content hash. Normalisation folds case,
accents, punctuation and whitespace, and nothing more.

An empty digest is never sent. `build_digest` returns `None` rather than an
object with no rows, so "send nothing" is not a decision a caller can forget to
make. A run that suppressed an empty digest is still recorded, so it stays
distinguishable from a run that never happened.

## Dry run

The default. `--send` is the only way to turn it off, because this file will
eventually be invoked by a scheduler with whatever arguments someone typed months
earlier.

```
cd apps/api
uv run python -m job_os.scripts.run_job_alerts --dry-run
uv run python -m job_os.scripts.run_job_alerts --dry-run --out /tmp/digest.txt --html-out /tmp/digest.html
uv run python -m job_os.scripts.run_job_alerts --dry-run --force --subscription-id <uuid>
uv run python -m job_os.scripts.run_job_alerts --dry-run --now 2026-08-12T08:00:00+00:00
```

A dry run writes nothing: not the digest row, not the sent log, not
`last_sent_at`. That matters more than it looks. A dry run that recorded sends
would poison the dedupe ledger and the first real digest would arrive empty.

`--html-out` concatenates the HTML parts into one file for eyeballing in a
browser. No mail client would accept that file as a message; it is for reviewing
layout.

`POST /api/v1/alerts/subscriptions/{id}/preview` runs the same code path in dry
run mode, so the preview in the app cannot disagree with the email.

Exit codes: 0 clean, 1 a subscription failed, 2 the run could not start.

## Layout

| File | What it does |
|---|---|
| `db/models/alert.py` | `alert_subscriptions`, `alert_digests`, `alert_sends` |
| `alembic/versions/20260812_0000_job_alerts.py` | the migration |
| `integrations/email.py` | `EmailTransport`, console and Resend implementations |
| `services/alert_tokens.py` | signed unsubscribe tokens |
| `services/alert_freshness.py` | the date labels |
| `services/alert_schedule.py` | is this subscription due, in the user's clock |
| `services/alert_digest.py` | dedupe, compose, render text and HTML |
| `services/alert_runner.py` | the run: find, build, send, record |
| `scripts/run_job_alerts.py` | cron entrypoint |
| `routers/alerts.py` | subscription CRUD, preview, public unsubscribe |
| `.github/workflows/job-alerts.yml` | the schedule, commented out |

Alert preferences live on `alert_subscriptions`, not in `users.settings`. A
scheduled job and an unauthenticated unsubscribe request both have to read and
write them, and a blob on the user row is the wrong shape for either.

The email HTML is nested tables with inline styles and nothing newer than CSS
2.1. Outlook renders mail through Word, where a flexbox layout collapses into a
column of unstyled text. Width is fixed at 600px, which every client has agreed
on since roughly 2005.

## Timezones

Subscriptions store an IANA zone name, not a UTC offset, and every hour field is
a local hour in that zone. That is the difference between "08:00 my time"
surviving a daylight saving change and drifting an hour twice a year. An
unresolvable zone falls back to UTC at run time with a warning, because a bad
string should cost someone an hour and not their alerts, but the API rejects one
at write time so nobody silently gets 08:00 UTC after asking for 08:00 local.

Quiet hours gate the `immediate` cadence only. A daily digest already carries an
hour the user chose, and letting quiet hours veto it would mean someone who
picked 23:00 silently never gets mail and nothing says why.

## What is left to enable this live

In order. Nothing below has been done.

1. **Merge order.** This migration's `down_revision` is `0006_resume_engine`, and
   so is every sibling branch's. Whichever merges second has to repoint its own
   `down_revision` at whatever landed first, or Alembic sees multiple heads. The
   revision id here is `job_alerts_email_digest` rather than `0007` so that the
   id itself does not collide.
2. **Run the migration** against the target database.
3. **Verify a sending domain with Resend** and set up SPF, DKIM and DMARC on it.
   Unauthenticated mail to Gmail at any volume goes to spam or is rejected.
   Confirm DKIM covers the `List-Unsubscribe` and `List-Unsubscribe-Post`
   headers, which RFC 8058 requires for one-click.
4. **Set the secrets and variables**: `RESEND_API_KEY`, `EMAIL_FROM`,
   `ALERT_UNSUBSCRIBE_SECRET` (48 random bytes, and rotating it invalidates every
   outstanding unsubscribe link), `ALERT_LINK_BASE_URL` pointing at the API
   origin, `ALERT_APP_BASE_URL`, and `ALERT_POSTAL_ADDRESS`.
5. **Dry run against production data** with `--force` and read the artifact.
   Check the freshness labels against listings you can verify by hand. This is
   the step that catches a bad `first_seen_at` before anyone is mailed.
6. **Send one digest to yourself**: create a subscription on your own account,
   set `ALERTS_ENABLED=true`, and run with `--send --subscription-id <yours>`.
   Check it in Gmail and in Outlook, and click the unsubscribe link while signed
   out, which is the case that matters.
7. **Enable the cron** by uncommenting the `schedule:` block in
   `.github/workflows/job-alerts.yml`. Hourly, not daily: each subscription
   decides whether its own local send hour has arrived, and a daily tick would
   collapse every timezone onto one UTC hour.
8. **Watch the first week.** `alert_digests` carries status, provider message id
   and error per attempt, and `deduped_count` is the number that tells you
   whether dedupe is working.

Not built, and worth knowing about before this carries real volume: no bounce or
complaint webhook, so a hard-bouncing address is retried forever; no per-user
send cap beyond the cadence; and no UI for managing subscriptions, which are
currently reachable only through the API.
