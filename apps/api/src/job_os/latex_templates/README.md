# Bundled LaTeX resume templates

Six templates, each in its own directory. A directory holds:

- `template.tex.j2` - the LaTeX, with placeholders. Written for job.os.
- the class file and fonts the template needs, if any. Vendored so a render
  never depends on fetching anything.
- the upstream licence, kept as it was received.
- `ATTRIBUTION.md` - where it came from, under what licence, and every change
  made to it.

Rendering is `apps/api/src/job_os/services/latex_render.py`. The catalogue, the
per-template notes shown in the app and the sample data used for previews are in
`latex_catalog.py`.

## The engine constrains what these can do

Tectonic is a patched XeTeX and nothing else. There is no pdflatex and no
lualatex, so `\ifPDFTeX` is always false, `\ifluatex` is always false, and
pdfTeX primitives such as `\pdfgentounicode` do not exist. Its package bundle is
TeX Live 2022, which has `fontawesome5` but not `fontawesome6`, and
`SourceSansPro-*` but not Source Sans 3.

Two rules follow, and both are why several of these templates needed changes:

1. **Name fonts by file, not by family.** `\setmainfont{Roboto}` asks the host
   operating system, which in a container has no fonts installed.
   `\setmainfont{Roboto-Regular.otf}` is served from Tectonic's bundle.
2. **Assume no shell escape.** Renders run with `--untrusted`, so anything that
   shells out (pdfx minting a timestamp through texlua, for instance) fails.

## Placeholders

The template language is Jinja with LaTeX-safe delimiters, because LaTeX already
owns `{`, `}` and `%`:

| purpose | syntax |
| --- | --- |
| a value | `<<name>>` |
| a block | `<% if work %>` ... `<% endif %>` |
| a comment | `<# not rendered #>` |

Note that a LaTeX `%` comment does **not** hide a placeholder: Jinja runs first
and has no idea what a LaTeX comment is.

Every value a template receives is already LaTeX-escaped, and a template cannot
reach an unescaped one. The names available are whatever
`latex_render.build_render_model` returns; that function is the contract, and
the six templates here, a template stored in Appwrite, and the prompt that asks
a model to write one from an upload all have to agree with it.

## Licences

| template | licence | notes |
| --- | --- | --- |
| jakes | MIT | licence file kept |
| sb2nov | MIT | licence file kept, example content not reused |
| awesome-cv | LPPL-1.3c | class renamed on modification, as LPPL requires |
| altacv | LPPL-1.3+ | class renamed on modification, as LPPL requires |
| moderncv | LPPL-1.3c | nothing vendored, ships in Tectonic's bundle |
| deedy | Apache-2.0, fonts SIL OFL 1.1 | OFL text added, upstream omits it |

All six permit commercial use. Awesome-CV's example `.tex` files are CC BY-SA,
which is why `awesome-cv/template.tex.j2` was written from the class's
documented commands rather than derived from `examples/resume.tex`.
