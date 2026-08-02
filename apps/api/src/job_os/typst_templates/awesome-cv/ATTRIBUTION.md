# Awesome-CV, Typst port

- Upstream: https://github.com/posquit0/Awesome-CV.
- Author: Claud D. Park (posquit0).
- Licence: LPPL-1.3c. `LICENCE` in this directory is the upstream file,
  unchanged.
- Fonts: Source Sans Pro (SIL OFL 1.1), Roboto (Apache-2.0) and Font Awesome 5
  Free (SIL OFL 1.1), all from `typst_templates/_fonts/`. See the ATTRIBUTION.md
  there for where each came from.

Written against the class's documented commands rather than derived from the
upstream examples, which are CC BY-SA. That is the same care the LaTeX version
took and the reason neither document reuses `examples/resume.tex`.

The reference for this port is not the LaTeX template but the class it drives,
`latex_templates/awesome-cv/awesome-cv-tectonic.cls`. Every colour and size here
was read out of it.

## What was reproduced

- `\geometry{left=1.4cm, top=.8cm, right=1.4cm, bottom=1.2cm}`.
- The palette: text `#333333`, graytext `#5D5D5D`, lighttext `#999999`,
  darktext `#414141`, the section divider `#5D5D5D`, and the accent, which this
  template sets to `awesome-darknight` `#131A28`.
- The header stack: the given name at 32pt in Roboto Light and the family name
  at 32pt in Roboto Bold, then the position at 7.6pt in small caps, the address
  at 8pt italic, and the social line at 6.8pt.
- `\sectionstyle`: 16pt bold with a rule running from the end of the title to
  the right margin, on the class's own 3mm and 2.5mm skips.
- `\cventry`, including its argument order, which is easy to get backwards: the
  bold line is the employer or the school and the small-caps line beneath is the
  role or the degree, not the other way round.
- `\cvskill` as a right-aligned bold label against its values, and `\cvhonors`
  as the class's three-column table, a 1.5cm date, the title, and a 2.5cm
  right-aligned source.
- The Font Awesome contact icons. Kept rather than dropped, because they are
  the design, and because this template's ATS note already warns out loud that
  they are glyphs and that a parser may read a stray character beside the
  address.

## Differences from the LaTeX render

- **This fits on one page where Tectonic needs two.** On the sample resume the
  LaTeX render pushes the Languages section onto a second page and this one does
  not. That is the compactness difference showing up at a page boundary rather
  than as a slightly different gap. For a resume it is probably an improvement,
  but it is a real difference and it is the reason to look at a long document
  before trusting this template with one.
- **The text layer is correct here and is not in the LaTeX version.** Extracting
  text from the Tectonic render gives "BACKEND AND AI ENGiNEER" and "MASTER OF
  SCiENCE, COMPUTER SCiENCE": the class fakes small capitals and the resulting
  ToUnicode map emits a lowercase i in the middle of every capitalised word. The
  page looks right, so no reader would notice, and an applicant tracking system
  reads exactly that layer. The Typst render extracts as typed.
- **No hyphenation**, so a long line wraps rather than breaking a word.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of `services/typst_render.py`.
