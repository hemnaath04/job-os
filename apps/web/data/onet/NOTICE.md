# O\*NET source data

The two gzipped CSVs in this directory are unmodified extracts from the O\*NET
Database, version 30.3.

> This product uses public information provided by the O\*NET Program: O\*NET
> 30.3 Database by the U.S. Department of Labor, Employment and Training
> Administration (USDOL/ETA). Used under the CC BY 4.0 license. O\*NET is a
> registered trademark of USDOL/ETA. job.os has modified this information and
> USDOL/ETA has not approved, endorsed, or tested these modifications.

License: Creative Commons Attribution 4.0 International
(https://creativecommons.org/licenses/by/4.0/).

## Files

| File                                | Rows   | Columns                                                       |
| ----------------------------------- | ------ | ------------------------------------------------------------- |
| `job_titles.csv.gz`                 | 57,543 | `O*NET-SOC Code, Title, Job Title, Short Title, Source(s)`     |
| `sample_of_reported_titles.csv.gz`  | 7,953  | `O*NET-SOC Code, Title, Reported Job Title, Shown in My Next Move` |

`job_titles.csv` is O\*NET's alternate-titles list: every occupation plus the
title variants employers actually use for it. `sample_of_reported_titles.csv` is
the smaller list of titles survey respondents reported for themselves. Both are
used purely as alias raw material, never as the taxonomy itself.

The files are committed gzipped (525 KB total, vs 5.2 MB uncompressed) so the
taxonomy build has no external dependency. `scripts/build-taxonomy.ts` reads
`.csv.gz` and plain `.csv` interchangeably.

## Refreshing for a new O\*NET release

1. Download the CSV bundle for the new version from
   https://www.onetcenter.org/dl_files/database/db_30_3_csv/ (swap the version
   segment, for example `db_31_0_csv`).
2. Copy `Alternate Titles.csv` here as `job_titles.csv` and
   `Sample of Reported Titles.csv` as `sample_of_reported_titles.csv`, then
   `gzip -9` both.
3. Bump `ONET_VERSION` in `apps/web/src/lib/taxonomy/spec.ts`.
4. Run `pnpm --filter @job-os/web taxonomy:build`. The generator fails loudly if
   a SOC code referenced by the hand-authored leaf layer has disappeared from
   the release, which is the signal that a crosswalk needs re-checking.
5. Run `pnpm --filter @job-os/web test` and review the generator's unmapped and
   collision reports before committing the regenerated artifacts.

To build against a checkout of the CSVs kept outside the repo, set
`ONET_DATA_DIR=/path/to/dir`.
