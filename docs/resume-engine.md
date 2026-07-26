# Resume Studio

Resume Studio is the evidence-backed resume workflow inside job.os. JSON Resume
is the canonical document. PDF and LaTeX are generated artifacts, not the
editable source of truth.

## Workflow

```text
iCloud PDF, DOCX, or JSON
          |
          v
editable JSON Resume + immutable version
          |
          +--> structured manual edit --> child version
          |
          +--> conversational AI proposal
                    |
                    v
        verified profile facts + current GitHub READMEs
                    |
                    v
             user accepts proposal
                    |
                    v
          independent quality-model review
                    |
                    v
       one-page and selectable-text PDF checks
                    |
             score >= 90, no blockers
                    |
                    v
              final stored resume
```

## Data preservation

- The migration only adds columns and the revision-message table. Existing
  resume and application rows are not replaced.
- Every edit creates a child `resume_versions` row linked by
  `parent_version_id`.
- Final PDF bytes, portable LaTeX, review results, source filename, and revision
  note are stored with the version.
- Role-specific resumes and versions can be renamed or archived. Archive
  removes them from the active library without erasing stored history.
- The master resume itself cannot be archived, and its last version is
  protected. Importing a new master creates another version.

## Evidence rules

- New employers, dates, coursework, metrics, technologies, credentials, or
  outcomes are rejected unless they already exist in the resume or verified
  profile facts.
- Project review fetches current public README text for included GitHub
  projects. Known Hemnaath projects are resolved even when an imported resume
  contains only the project name.
- TypeScript, React, and Next.js are allowed only as verified project context.
  The resume is positioned around AI/ML, backend and systems, LLM agents, and
  test automation rather than frontend engineering.
- Unsupported job requirements become gaps or suggestions, never invented
  bullets.

## Quality gate

AI chat first stores a proposal. After the user accepts it, the resulting
revision is reviewed by a second model. The finalizer repeats the review and
blocks completion unless:

- the combined score is at least 90;
- no issue has `blocking` severity;
- the PDF is exactly one page;
- the PDF contains selectable text;
- required contact fields and professional experience are present.

Review failures are retained on the version so the user can continue revising
in chat, accept another suggestion, and review again. Drafts remain previewable,
but PDF download and export are available only for finalized versions.

## iCloud import

A browser cannot silently enumerate iCloud Drive. Use **Set master** for the
canonical file or **Import iCloud folder** to authorize the whole synced resume
folder in Brave. Imports accept up to 30 resume files per batch. The files
remain in iCloud; job.os stores editable copies and version metadata in
Postgres.
