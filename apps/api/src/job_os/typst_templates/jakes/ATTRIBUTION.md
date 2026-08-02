# Jake's Resume, Typst port

- Upstream: https://github.com/jakegut/resume (`resume.tex`), archived August 2024.
- Author: Jake Gutierrez.
- Licence: MIT. `LICENSE` in this directory is the upstream file, unchanged.
- Vendored here: nothing beyond the licence. The type is New Computer Modern,
  which Typst embeds, so this template needs no font files at all.

This is a port, not a translation. The LaTeX original is
`latex_templates/jakes/template.tex.j2` and remains the reference: where the two
disagree, that one is right and this one has a bug.

## What was reproduced

The design, measured off the LaTeX render rather than guessed at: US Letter with
half-inch margins, Computer Modern at 11pt, a centred bold small-caps name over
a bar-separated contact line, small-caps section headings underlined by a rule,
four-cornered entries with the title and date on one line and the employer and
place italicised beneath, and section bodies indented by 0.15in with bullets
indented further.

Two upstream decisions were deliberately kept because they were deliberate
there:

1. **No hyphenation.** The LaTeX version sets `\hyphenpenalty` and
   `\exhyphenpenalty` to 10000, because a resume is mostly proper nouns and a
   real render produced "Mon-goDB". `hyphenate: false` is the same instruction.
2. **A wrapping first column.** The LaTeX version uses `tabularx` rather than
   `tabular*` so a long job title cannot collide with its date. A grid whose
   first column is `1fr` behaves the same way.

## Differences from the LaTeX render

- **Slightly tighter vertically.** On the sample resume this port ends about 6%
  higher up the page than Tectonic does. The two engines break paragraphs and
  space lists by different rules and closing that gap exactly would mean
  hard-coding spacing that only holds for one document. A resume near the
  one-page boundary may therefore fit here and not there.
- **The en dash is a real character.** LaTeX turns `--` into an en dash through
  a ligature Typst does not have, so the render model emits U+2013 directly.
  The printed result is the same glyph.
- **No `\underline` on project links.** Both versions link project names; only
  the header underlines, which matches where the LaTeX original puts its one
  `\underline`.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of
`services/typst_render.py`; it is the whole reason this is safe.
