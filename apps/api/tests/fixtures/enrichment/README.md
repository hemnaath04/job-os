# Enrichment fixtures

Four real jobs, captured from two live products and mapped field by field into
`JobEnrichment`. None of them is invented. A schema tested only against data
written to fit it is a schema that passes its tests and fails its ingest path.

Source payloads live outside the repo, in `~/Documents/job-os-research/evidence/`:

| Fixture | Source payload | Record |
| --- | --- | --- |
| `cisco_cloud_engineer.json` | `jobright_job_detail.json` | `dataSource.jobResult`, jobId `6a7b3f32ecf5194164fbcbee` |
| `worlds_ml_research_intern.json` | `hiringcafe_ssr_payload.json` | `ssrHits[19].v5_processed_job_data` |
| `vienna_fullstack_engineer.json` | `hiringcafe_ssr_payload.json` | `ssrHits[16].v5_processed_job_data` |
| `first_tee_play9_intern.json` | `hiringcafe_ssr_payload.json` | `ssrHits[1].v5_processed_job_data` |

## Why these four

They were chosen to span the shapes that break a scorer, not to be
representative:

| Fixture | Skills named | Weight pool | Role | What it exercises |
| --- | --- | --- | --- | --- |
| Cisco Cloud Engineer | 68 | 190 | senior, 5 years stated | A dense posting. The skills axis has to spread 45 points across 68 requirements, so some misses round to under a point. Also the only fixture with prose and atomized requirements both, and with compound skill names like "Cloud Computing AWS". |
| Worlds ML Research Intern | 16 | 68 | new-grad, no years stated | Bachelors AND masters AND doctorate all `Required` at once. Transparent pay. Genuinely remote. |
| Vienna Full Stack Engineer | 6 | 30 | mid, no years stated | Sits just below the sparse floor of 32, so the thin-posting line fires on an otherwise ordinary tech job. Currency and frequency present with no figures, which is the common shape for hidden pay. |
| First Tee Play 9 Intern | 1 | 4 | new-grad | The sparse non-tech posting. This is the shape of the failure that produced `fit-score.ts`'s floor: a nearly-empty JD that a coverage ratio rewards for saying nothing. |

## Mapping notes

Both references are richer in some places than this schema and poorer in others.
Where the mapping had to make a decision, it is recorded here rather than left
for a future reader to reverse engineer from the JSON.

**Seniority.** hiring.cafe's five values collapse onto this schema's bands:
`No Prior Experience Required` and `Entry Level` both become `new-grad`,
`Mid Level` becomes `mid`, `Senior Level` becomes `senior`, and `None` becomes
`unknown`. Their vocabulary has no staff, principal or director band at all.

**Skills.** hiring.cafe exposes no per-job importance and no required-versus-
preferred split. Their `technical_tools` is the only atomized ask they publish,
so it maps to `skills` with importance 2 and necessity `required`, with a few
importance overrides where the posting's own summary makes a tool central. This
understates what a real enrichment pass produces and is the right way round: the
fixtures should not flatter the schema.

Jobright does publish both, and its record maps almost directly:
`jdCoreSkills[].score` becomes `importance`, `jdCoreSkills[].type` becomes
`kind`, and membership in `detailQualifications.mustHave.hardSkill` decides
`necessity`. Their skill names are compound ("Cloud Computing AWS",
"Networking TCP/IP"), which is why the matcher needs token containment and not
just set equality.

**Compensation.** Taken from hiring.cafe's `yearly_*` pair and labelled yearly,
NOT from their `listed_compensation_frequency`. Those two disagree in their own
data: `ssrHits[19]` carries `listed_compensation_frequency: "Hourly"` with the
yearly figures populated at 62400 and the hourly figures left null. The yearly
pair is the reliable one across the sample, so it is what the fixtures use.

The conversion basis in `schemas/enrichment.py` was recovered from their numbers
rather than assumed. `ssrHits[0]` lists $15/hour as yearly 31200, monthly 2600,
weekly 600, bi-weekly 1200 and daily 120, which is exactly 2080 hours a year, 52
weeks, 26 fortnights, 12 months and an 8 hour day.
`test_compensation_derivation_matches_the_reference_exactly` pins those figures.

**Visa sponsorship.** hiring.cafe's boolean cannot distinguish a refusal from
silence, so a `False` there maps to `not-mentioned` here and never to `no`.
Mapping it to `no` would have invented a refusal that the posting never made,
which for an international candidate is the most consequential possible error.
Jobright's `isH1bSponsor: true` maps to `yes`, which it can, because that field
is a positive claim.

**Publish dates.** Both sources give a date and neither says how it was
established, so every fixture keeps `publish_date_is_estimated: true`.

## Regenerating

The fixtures are committed output, not generated at test time, so a change to the
schema shows up as a diff to review rather than as fixtures that silently follow
the code. Reproduce by re-running the mapping described above against the
evidence payloads; `tests/test_enrichment_schema.py` is what proves the result
still validates.
