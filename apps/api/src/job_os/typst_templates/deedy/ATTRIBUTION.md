# Deedy, Typst port

- Upstream: https://github.com/deedydas/Deedy-Resume, the OpenFonts variant.
- Author: Debarghya Das.
- Licence: Apache-2.0. `LICENSE` in this directory is the upstream file,
  unchanged.
- Fonts: Lato and Raleway, both SIL OFL 1.1. Not duplicated here. This template
  is rendered with the copies already vendored at
  `latex_templates/deedy/fonts/`, which carry the OFL text that the upstream
  repository omits, and the renderer adds that directory to Typst's font path
  (see `typst_render._font_dirs`). One copy of the fonts, one copy of the
  licence.

This is a port, not a translation. The LaTeX original is
`latex_templates/deedy/template.tex.j2` and its class is
`latex_templates/deedy/deedy-resume-openfont.cls`; that class is the reference
for every measurement below.

## What was reproduced

Read off the class rather than guessed at:

- margins `hmargin=1.25cm, vmargin=0.75cm`, and the 1/3 to 2/3 column split;
- the four colours, `#666666` for dates, `#2b2b2b` for body, `#6a6a6a` for
  headings and `#333333` for subheadings;
- the name at 40pt, the first part in Lato Hairline and the second in Lato
  Light, over a Raleway Medium contact line and a full-width rule;
- section titles in Lato Light 16pt uppercase, subheadings in Lato Bold 12pt
  uppercase, descriptors in Raleway Medium 11pt small caps, locations in Raleway
  Medium 10pt;
- the 8pt gap between sections.

The class pins a baseline skip on most of those (16pt/24pt for a section,
11pt/13pt for a descriptor, 10pt/12pt for a location). Typst does not infer
that, so the leading is set explicitly; without it a heading that wraps to two
lines opened up far wider than the LaTeX one.

The known upstream behaviour is kept rather than fixed: a column whose contents
exceed the page runs onto a second page instead of being balanced.

## Differences from the LaTeX render

- **The text layer is correct here and is not in the LaTeX version.** Extracting
  text from the Tectonic render of this template yields "it is not a
  realresume", "a nightly manualregression pass" and "a suite thatruns in 11
  minutes": spaces are dropped between runs. The page looks right, so this is
  invisible to a human reader and very visible to an applicant tracking system,
  which reads exactly that layer. The Typst render extracts cleanly. Given this
  is already the riskiest of the six for automated parsing, that is the more
  valuable of the two differences on this page.
- **No hyphenation.** The LaTeX version hyphenates "EXAMPLE INSTITUTE OF
  TECH-NOLOGY" in the sidebar; this one wraps the whole word instead.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of `services/typst_render.py`.

Two Typst traps met while writing this one, both silent:

1. A bare `[ ]` between two `text()` calls in code mode collapses to nothing,
   which ran the name together as "JordanA. Sample". Use `h(0.4em)`, or put the
   space inside the string.
2. `\:` is not an escape, so `[#name\://]` fails to parse. Build such a string
   in code mode instead.
