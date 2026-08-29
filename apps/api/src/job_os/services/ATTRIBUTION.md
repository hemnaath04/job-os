# Attribution for service modules

Upstream work that job.os builds on outside the template directories, which
carry their own ATTRIBUTION.md files.

## ats_profiles.py

### sunnypatell/ats-screener

- Upstream: https://github.com/sunnypatell/ats-screener
- Licence: MIT
- Author: Sunny Patel
- Documentation read: https://ats-screener.vercel.app/docs/scoring/

**What was taken.** The six-dimension scoring model (formatting, keyword match,
section completeness, experience relevance, education match, quantification),
the per-platform weight matrix, the parsing-strictness multipliers, the three
matching strategies (exact, fuzzy, semantic), the pass thresholds, and the
formatting deduction values. These are reproduced in `ats_profiles.py` as
constants with the platform names attached.

**What was not taken.** No code. ats-screener is a TypeScript project and job.os
reimplements the scoring in Python against its own JSON Resume documents. The
two will not produce identical numbers, and are not meant to: see below.

**What was changed, and why.**

- *Formatting is scored from the template, not from the PDF.* ats-screener has
  to infer layout from an uploaded file. job.os renders the file itself from one
  of eight known templates, so the column count comes from the template
  catalogue and is a fact rather than an inference.
- *Four of the nine formatting deductions are computed, five are not.* None of
  the bundled templates emit tables or images, so those checks would be
  theatre. The unchecked five are named in the report (`not_checked`) rather
  than silently scored as clean.
- *Keyword match is job.os's own number.* `evaluate()` takes the keyword score
  as an argument instead of recomputing it. job.os already scores the share of
  the posting's parsed must-have requirements the document claims, with
  word-boundary matching that handles C++, CI/CD and .NET. Substituting a second
  matcher would produce two coverage numbers that disagree with each other.
- *Experience relevance is deterministic.* ats-screener uses a model call.
  job.os scores quantification and the absence of passive openers with a regex,
  because this runs inside the tailoring loop where a round trip costs seconds.
  It measures less than the upstream dimension does.
- *The generic profile is job.os's own.* Its weights are the unweighted mean of
  the six platforms and its threshold is their median. It is what gets used for
  a posting that is not on any of the six, per the product decision that
  everything outside the modelled set falls back to job.os's own logic.

### srbhr/Resume-Matcher

- Upstream: https://github.com/srbhr/Resume-Matcher
- Licence: Apache-2.0
- Authors: Saurabh Rai and contributors

**What was taken.** The framing only: score a resume against the specific
posting rather than against a general style rubric, and report the gap as
named missing terms rather than as a grade. job.os already worked this way
before this module existed; Resume-Matcher is cited because it was read as part
of the design and because it is the reference implementation of the approach.

**What was not taken.** No code, no models, no vector store. job.os does not
use its embedding-based similarity scoring.
