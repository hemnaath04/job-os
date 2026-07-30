# Resume Studio

Resume Studio is the evidence-backed resume workflow inside job.os. JSON Resume
is the canonical document. PDF and LaTeX are generated artifacts, not the
editable source of truth.

## Workflow

```text
PDF, DOCX, or JSON
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

## Rendering

The resume JSON is the document; the PDF is real LaTeX, compiled by Tectonic in
the API container.

- Six templates ship with the app: Jake's Resume, sb2nov, Awesome-CV, AltaCV,
  ModernCV and Deedy. Each is vendored with its upstream licence and an
  `ATTRIBUTION.md` recording every change, under
  `apps/api/src/job_os/latex_templates/`. Jake's is the default: single column,
  no icon glyphs, most likely to survive a parser.
- Every resume value is LaTeX-escaped once, in `build_render_model`, so a
  template only ever receives escaped strings. Compiles run in a scratch
  directory with `--untrusted` and a scrubbed environment, because a stored
  template is untrusted input this process is about to execute.
- The image compiles all six at build time, which bakes Tectonic's package cache
  in and fails the build if a template stops compiling. Requests then render
  offline with `--only-cached`.
- A custom template comes from an upload: a `.tex` keeps the design exactly, a
  `.pdf` is rebuilt as LaTeX and comes close rather than matching. Either way it
  has to compile with sample data before it is stored, and a failure goes back
  to the model with the compiler's own log, up to four attempts.
- Previews are the render: each template's card shows the PDF produced from
  clearly invented sample data, never the user's own history.
- The Appwrite agent function has no LaTeX engine. It returns a review with no
  page count rather than failing, and the browser gets the PDF from the
  container.

Seeding and migrating the template rows is
`python -m job_os.scripts.seed_latex_templates`, which is idempotent and
additive: it adds columns, and tags the two rows from the retired HTML renderer
`legacy_html` instead of deleting them.

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

## Resume library import

Use **Set master** for the canonical file or **Import folder** to add an
existing resume library. Imports accept up to 30 resume files per batch.
Original files stay on the user's device; job.os stores editable copies and
version metadata.
