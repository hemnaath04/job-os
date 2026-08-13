# Job enrichment and browse-time match scoring

## The problem this fixes

Two scorers existed, and the good one was in the wrong place in the funnel.

The score a user saw while **browsing** came from `apps/web/src/lib/discover/fit-score.ts`:
a 118-entry hand-written keyword lexicon, matched against the JD text, scored as
`matched / max(named_skills, 8)`. It runs client-side, costs nothing, and answers
one narrow question, how many of the words it happens to know appear in both the
posting and the profile.

The scorer that actually judges fit is the 2,831-line requirement-coverage
pipeline in `apps/api/src/job_os/services/tailor.py`. It only fires **after** the
user has already committed to tailoring one specific job. By the time it has an
opinion, the user has already chosen.

So the expensive, honest scorer never helped anyone decide what to look at, and
the cheap, shallow one made every decision.

## The fix

Split the work by what it depends on.

```
                    once per job, at ingest                per user per job, at browse
                  ┌──────────────────────────┐           ┌───────────────────────────┐
  raw posting ──► │  job_enrich.enrich_job   │ ──facts──►│  job_match.score_job      │──► 0-100
                  │  ONE LLM call            │           │  no LLM, no network, pure │    + breakdown
                  └──────────────────────────┘           └───────────────────────────┘
                       O(jobs), costs money                    O(1), costs nothing
```

Everything that does not depend on who is looking gets extracted once and stored.
Matching then reduces to set intersection and integer arithmetic. Scoring one
profile against fifty thousand jobs costs zero model calls.

This is the shape both market leaders converged on. hiring.cafe attaches
`v5_processed_job_data` to every job, 91 precomputed fields. Jobright ships
`jdCoreSkills` with per-skill importance and keeps requirements in both prose and
atomized form.

## What was built

| File | Role |
| --- | --- |
| `apps/api/src/job_os/schemas/enrichment.py` | The schema. 26 top-level fields, versioned from the first row. |
| `apps/api/src/job_os/services/job_enrich.py` | The worker. One LLM call per job through the existing Manifest plumbing. |
| `apps/api/src/job_os/services/job_match.py` | The scorer. Four axes, one number, full attribution. |
| `apps/api/tests/fixtures/enrichment/` | Four real jobs from the two reference payloads, with provenance. |

## Where the data lives

On the existing `Job.jd_parsed` JSONB column, under the `enrichment` key.

No migration, so writing can start without downtime, and whatever `jd_parse.py`
wrote at import stays readable beside it rather than being overwritten. The fields
that turn out to deserve an index can be promoted to real columns later, with
production evidence about which ones those are instead of a guess now.

`load_enrichment` returns `None` for a document it cannot validate, including one
written by a newer schema version. A rollback therefore finds jobs it cannot read
rather than a server that will not start, and those jobs fall onto the same path
as jobs that were never enriched, which already exists and already works.

## The scoring formula

```
overall = clamp(0, 100, 100 + sum(every line item's points))
```

Four axes, each with a budget it spends on reasons the fit is not perfect:

| Axis | Weight | Deducts for |
| --- | --- | --- |
| skills | 45 | each requirement the profile does not cover, weighted by importance and by required-versus-preferred; plus a thin-posting share |
| experience | 25 | years short of a stated minimum, or seniority bands short when no years are stated; management experience; being well past the band |
| education | 15 | degree levels short, a degree in progress rather than held, preferred degrees missing, fields of study not covered |
| industry | 15 | no overlap with the candidate's industry history, at a lower rate when there simply is no history on file |
| bonus | 0 | *credits*: title match, sponsorship offered, commitment match, remote match. Capped at 15. |

Weights say what the product believes: what you can do matters more than how long
you have been doing it, and both matter more than where you did it.

### Why it counts downwards

A ratio built upwards ("you matched 4 of 11 things") cannot say why the other 7
cost what they cost. Counting down from a perfect match forces every lost point to
name the thing that took it. The base of 100 is itself the named reason: start
from a perfect match and account for every departure from it.

Each axis is **budgeted** rather than clamped. A deduction that does not fit is
truncated to what is left and says so. That is what keeps the line items summing
to the axis total; a clamp applied afterwards would leave lines that add up to
more than the axis actually lost, and the breakdown would stop explaining the
score. It also makes a negative total unreachable by construction, since the four
budgets sum to exactly 100.

### The invariant

```
100 + sum(line.points for every line) == raw_overall == sum(axis.points for every axis)
```

Asserted in `test_job_match.py` on every fixture crossed with every profile, both
flat and per axis. The two directions catch different bugs: the flat sum catches a
missing line, the per-axis sum catches a line filed against the wrong axis.

Per-line integers are allocated by **largest remainder**, not by rounding each
line independently. Independent rounding drifts by a point or two, and that drift
is precisely the unexplained residual the design exists to avoid.

## Worked example

The Cisco Cloud Engineer fixture, real data, against a profile with three years
of backend and test automation, a bachelors in CS held, a masters in progress, no
Kubernetes and no Go.

```
65 overall, starting from 100 and accounting for:
  skills: 11/45
    -1: missing 5+ years software engineering experience (required)
    -1: missing Go (required)
    -1: missing Kubernetes (required)
    ... 52 more named requirements, 22 of them recorded at zero points
        because 45 points across 68 requirements rounds some below one
  experience: 9/25
    -16: 2 years short of the 5 this posting requires
  education: 15/15
    note: profile holds a bachelors against the bachelors required
  industry: 15/15
    note: profile has background in AI Infrastructure, Artificial Intelligence
          (AI), Communications Infrastructure and 11 more
  bonus: 15/0
    +8: the title matches a target role on the profile (Cloud Engineer)
    +6: the posting states it sponsors visas and the profile needs sponsorship
    +1: the posting is full-time, which is what the profile is looking for
        (worth 4, capped at 1 by this axis's remaining budget)
```

`100 - 34 - 16 - 0 - 0 + 15 = 65`. Every point is on that list.

Two details worth pointing at:

The 22 zero-point lines. A dense posting spreads 45 points so thin that some
misses round below a single point. They are recorded at zero rather than dropped,
so no skill can appear on the card without a matching line explaining it. The
arithmetic is unaffected, and `top_reasons()` is what a card actually renders.

The capped bonus. The commitment match is worth 4 but only 1 point of headroom
was left, so it took 1 and the sentence says why. Without that note the same fact
would appear to be worth different amounts on two different jobs.

## Attribution as the differentiator

Jobright's own FAQ has an accordion titled "What is a Match Score and How is it
Calculated?" whose answer is not present in the served HTML at all. Their job
payload ships `recommendationScores` as three named features
(`q_seniority_match`, `q_job_skill_match`, `q_industry_match`), each with a bare
number and nothing that says how it was reached. In the captured record all three
are `0` and `industryMatchingScores` is an empty array.

Nobody in this market explains the number. Once the facts are precomputed,
explaining it is nearly free, and the explanation is the part a user can act on:
"minus 16: 2 years short of the 5 this posting requires" is a decision, where
"64% match" is a mood.

Every line carries a machine-readable `reason`, a human `detail`, the `subject` it
concerns, and where available the `evidence` phrase from the JD, so the breakdown
quotes the posting rather than asserting against it.

## Blockers are not points

A posting that will not sponsor a visa is not a worse match for a candidate who
needs sponsorship. It is an impossible one. Averaging a binary fact into a ranking
number is how a 90% match turns out to be unapplyable, so blockers travel beside
the score in `MatchScore.blockers` and deduct nothing.

## How this relates to `fit-score.ts`

**Decision: `job_match.py` becomes the path of record. `fit-score.ts` stays as the
client-side fallback for jobs that have not been enriched yet, and is not
deleted.**

Deleting it would regress every un-enriched job to no score at all. Enrichment is
O(jobs) against a corpus that grows continuously, so there is always a backlog,
and a job with no score is worse for the user than a job with a rough one.

The handoff contract, one job at a time:

- `jd_parsed.enrichment` present and its `schema_version` current: the server
  score wins and the client renders the breakdown. Authoritative.
- Otherwise: the client lexicon score, presented as a rough estimate, exactly as
  today.

The two must never both render for the same job.

### What was preserved

`fit-score.ts` divides by `max(named_skills, 8)` for a specific documented
reason: a mechanical engineering internship named only three skills, a backend
profile matched all three, and it scored 100% and outranked roles that were
genuinely a fit. A raw coverage ratio pays a posting for saying little.

That protection is ported, not dropped. `MIN_SKILL_WEIGHT_POOL = 32` is the same
floor of eight requirements expressed in the new scorer's currency (eight
ordinary required asks at 2 x 2 each). Four tests hold it, including a
reproduction of the original failure.

### What improved

The floor is now a **named line item** instead of a silent denominator. The old
scorer returned 37% for a three-skill posting and had nothing to say about the
missing 63 points. The new one says:

```
-28: the posting names only 3 skills, too thin to judge a match on
```

Same arithmetic, and now an answerable statement. `confidence` carries the same
signal structurally, so the UI can say "not enough detail to score" rather than
presenting a confident 43%.

### What the lexicon is no longer for

`fit-score.ts`'s 118 entries did two jobs: they enumerated which skills exist,
and they recorded which surface forms mean the same skill. The first job is
obsolete, since the model now reads skills straight off the posting and no list
has to be guessed in advance. The second is still essential and is carried by
`SKILL_ALIASES` in `schemas/enrichment.py`.

The alias table is smaller than the lexicon on purpose, and an entry only earns
its place when the two forms are genuinely the same skill rather than merely
adjacent. PyTorch and TensorFlow are both deep learning frameworks and are not
interchangeable on a resume.

Both sides of a match pass through `canonical_skill`, which is the only reason
set intersection is a legitimate way to compare them. A form that normalizes
differently on the two sides is a skill that can never match, and that failure is
invisible in production because it looks exactly like a candidate who lacks the
skill. Two tests guard it: every alias value must already be canonical, and
canonicalization must be idempotent.

That guard caught a real bug during development. With singularization applied
after alias resolution, `k8s` resolved to "kubernetes" while "Kubernetes" reduced
to "kubernete", so a posting saying k8s and a profile saying Kubernetes shared no
key.

### Known duplication

The alias knowledge now exists in two languages: `SKILL_ALIASES` in Python and
`LEXICON` in TypeScript. They will drift. The drift is bounded, because the
TypeScript path only runs for un-enriched jobs and only produces a rough
estimate, but it is real. The fix, when the fallback path stops mattering, is to
delete the TypeScript lexicon rather than to try to sync them.

## Integration seam

Deliberately not wired into the ingest pipeline here, because that pipeline is
being reworked in parallel and two agents editing the same call site would
collide. Three functions are the whole surface:

```python
from job_os.services.job_enrich import enrich_job, store_enrichment, load_enrichment

# at ingest, once per job
enrichment = await enrich_job(
    job.jd_clean, title_hint=job.title, company_hint=company_name, posted_at=job.posted_at
)
job.jd_parsed = store_enrichment(job.jd_parsed, enrichment)

# at browse, once per job per render, free
from job_os.services.job_match import CandidateProfile, score_job

enrichment = load_enrichment(job.jd_parsed)
if enrichment is not None:
    score = score_job(enrichment, candidate)  # candidate built once per request
```

`CandidateProfile.build` is the only supported constructor, because it
canonicalizes the free-text fields on the way in. A profile assembled by hand
from raw surface forms will silently match nothing.

Two things still to do before this serves traffic: build `CandidateProfile` from
`ProfileFact` rows (the web app's `buildProfileVocab` is the shape to follow), and
expose the breakdown on the discovery response so the client can render it
instead of its own estimate.

## Cost

One call per job at ingest, on the `job-os-sonnet` tier (Sonnet 5), with a 4096
token ceiling and 18000 characters of posting. Enrichment cost is the tier
multiplied by the corpus. Matching is free, forever, for every user.

`test_one_llm_call_per_job` asserts the count, because the economics of the whole
design rest on it and a comment would not fail the build.

## Failure policy

Enrichment sits in the ingest path, so `enrich_job` never raises. An ingest path
that can raise per job is an ingest path that loses jobs.

Partial extraction is salvaged per sub-object: a hallucinated compensation
frequency costs the job its salary figures and nothing else. A single
`model_validate` would have thrown the skills, the education and the eligibility
away along with the bad field, turning one wrong value into an unmatchable job.
Every partial is recorded in `extraction_gaps`, and the scorer reads that and
reports low confidence, so a failed enrichment surfaces as "not scored" rather
than as a job that scored badly.

Compensation is additionally sanity-checked against plausible annual bounds. A
model reading "$95/hour" as a yearly salary would otherwise put a real job into
salary filters at a hundredth of its actual pay, and nothing downstream would
question it.

## Schema decisions worth knowing

Full reasoning is in the `schemas/enrichment.py` docstring. The short version of
what departs from the references:

- **`visa_sponsorship` is tri-state.** Theirs is a boolean, so "we do not
  sponsor" and "the posting never mentioned it" are the same `False`. For an
  international candidate that is the most consequential distinction in the whole
  schema.
- **The six compensation frequencies are derived in Python**, not asked of the
  model. Arithmetic is exact and free here and merely likely there. It also
  avoids reproducing an inconsistency visible in the reference sample, where a
  row carrying `listed_compensation_frequency: "Hourly"` had its yearly figures
  populated and its hourly figures null.
- **`commitment` includes `co-op`**, which the reference has no value for. A
  co-op and a summer internship carry different eligibility rules and this
  product's users apply to both.
- **`enrolled_student_ok` and the in-progress degree case.** Neither reference
  models a candidate part way through the degree a posting requires, and for a
  product whose users are largely students that is most of the education axis.
- **`publish_date_is_estimated`** repeats in a value what the reference says only
  in a field name, so the honesty survives a consumer that reads values and not
  names.

Roughly 65 of the reference's 91 fields were cut: every physical and shift field,
every benefit boolean, the county and continent geo tiers with their `number_of_*`
counts, `language_requirements` (all 96 sampled jobs said English, so it carried
zero information), and `position_employer_type`. Those are filter facets for a
mass-market board covering warehouse and clinical work. None of them moves a
match score for a CS or AI role, and each one is a separate judgment for the model
to make, which is where enrichment cost actually goes.
