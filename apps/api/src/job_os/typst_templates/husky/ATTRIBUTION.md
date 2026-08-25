# husky ("Co-Op"), Typst port

- Upstream: none. Not a port of a public repository: the layout is the user's own
  personal resume `.tex`, supplied directly as the reference to match. There is
  no LICENSE file here because there is no upstream licence to carry.
- Author: the layout is the user's; the port for job.os was written here.
- Fonts: TeX Gyre Termes, GUST Font License, in `typst_templates/_fonts/texgyre/`.

## The font, which is the reason this port did not exist before

`latex_templates/husky/ATTRIBUTION.md` said this template could not be ported
honestly, because Typst's only embedded serif other than New Computer Modern is
Libertinus, and Libertinus is not Times. That was the right call about the
embedded faces and the wrong conclusion, because a face can be vendored.

`mathptmx` does not load Times. It loads URW Nimbus Roman No9 L, and the Tectonic
render says so: `pdffonts` on it lists `NimbusRomNo9L-Regu`, `-Medi`,
`-ReguItal` and `-MediItal`. TeX Gyre Termes is that same URW face with its
character set extended, under the same GUST licence as the Latin Modern already
vendored here. So the Typst render is set in the design the LaTeX page was set
in, rather than in a lookalike, and it embeds the four faces that answer to the
four Nimbus ones.

## What was reproduced

Measured off the Tectonic render at 110 and 400 dpi, and read off the reference
`.tex`, rather than eyeballed. The numbers below are the ones that were checked.

- The geometry: `top=0.45in, bottom=0.4in, left=0.6in, right=0.6in`, and
  `\pagestyle{empty}`, so no page number.
- `\Large\bfseries` for the uppercased name, which is 14.4pt at an 11pt base;
  `\small` (10pt) for the contact line; `\large` (12pt) for section headings.
- Single spacing, 13.5pt between baselines.
- The contact line's separator inside the box, as `\mbox{item $|$}` puts it, so
  a line that wraps leaves the bar at the end of the first line rather than
  starting the second with one. Both renders wrap the bundled sample's contact
  line in the same place.
- The section rule 3.27pt under the heading's ink, identical in both renders.
  The LaTeX `\titleformat` writes `\vspace{-2pt}` before `\titlerule` to cancel
  a gap titlesec adds and Typst does not, so copying that negative number put
  the rule through the middle of the letters.
- `\entry`'s two minipages at 0.72 and 0.27 of the text width. The fixed split
  matters: it is what makes "Jan 2026 -- May 2028 (expected)" wrap inside the
  right column, on the same two lines as the LaTeX render, instead of squeezing
  the title on the left.
- The spacing that gives the page its rhythm, all measured to within a point of
  the Tectonic render: 13.5pt from an entry's title to its subtitle, 15.6pt to
  its first bullet, 14.5pt between bullets, 19.6pt from a last bullet to the
  next entry, 16.6pt between skill rows, 11.7pt to the coursework line and
  28.3pt from there to the next education entry.
- The section order: Education, Technical Skills, Professional Experience,
  Projects. There is no summary, awards or languages section, because the
  reference file has none, and certificates are one more `\skillrow` labelled
  "Certifications" rather than a section.

## Differences from the LaTeX render

- **The first row after a section rule** sits 1.8pt lower than the Tectonic
  render puts it. In the LaTeX the gap from the rule to the first skill row is
  smaller than the gap between skill rows; Typst collapses adjacent block
  spacing to the larger of the two, so one of the pair has to give. The larger
  gap is the one that repeats, so it is the one that was matched.
- **The bullet dot is 1.5pt narrower.** The LaTeX label is `$\bullet$` from
  CMSY10, a maths font that is not vendored; this uses Termes' own U+2022. The
  left edge of the text after it is within half a point either way.
- **A page break keeps an entry with its bullets.** The blocks are
  `breakable: false`, as they are in every other port here, so on a document
  that runs to two pages an entry heading is never left alone at the bottom of
  the first. The LaTeX minipages are unbreakable too, but the bullets after them
  can break away, and on the long test document they do.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of `services/typst_render.py`.
