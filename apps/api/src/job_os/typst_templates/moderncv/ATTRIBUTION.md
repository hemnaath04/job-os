# ModernCV (banking), Typst port

- Upstream: https://ctan.org/pkg/moderncv.
- Authors: Xavier Danaux and the moderncv maintainers.
- Licence: LPPL-1.3c. Nothing is vendored from the class itself, so there is no
  licence file to carry here: the LaTeX side does not vendor it either, because
  the class ships inside Tectonic's own package bundle.
- Fonts: Latin Modern Sans, GUST Font License, in `typst_templates/_fonts/`.

## Read this before enabling it

This is the weakest of the six ports, and it is the only one where that is
structural rather than a matter of polish.

Every other template here was measured off a class file sitting in this
repository. moderncv has no such file: the design lives inside a fifteen-year-old
CTAN class that Tectonic supplies whole, so this is a reconstruction from the
class's output rather than a transcription of its source. Two values were read
out of the bundle's `moderncvcolorblue.sty`, which is real, and the rest of the
layout was measured off the rendered page.

It came out closer than expected. On the sample and on a real resume the two
renders agree on structure, on the palette and on where everything sits. But the
distinction matters for the future: a change in the class upstream would show up
in the Tectonic render and not here, and nothing in this repository would catch
that, because there is no local source to diff against. The other five ports do
not have that problem.

`typst_ready` is therefore left off, and enabling it is a decision to be made by
looking at the two pages, not by reading this file.

## What was reproduced

- `\usepackage[scale=0.8,top=1.4cm,bottom=1.4cm]{geometry}`: the text block is
  four fifths of the paper, so the side margins are a tenth each.
- The blue scheme, from the bundle's own `moderncvcolorblue.sty`:
  `color1 = rgb(0.22, 0.45, 0.70)` and `color2 = rgb(0.45, 0.45, 0.45)`.
- The banking header: the name in the accent colour beside the headline in grey,
  the address under it, then the contact line.
- `\moderncvicons{letters}` rather than the Font Awesome set, which is the one
  change the LaTeX template makes to the upstream example and for a good reason:
  under this engine the Font Awesome faces came out with no ToUnicode map, so a
  parser reading the contact line got a run of junk. Letters are ordinary glyphs
  and extract as text.
- `\cventry` in the banking arrangement: organisation and place bold on the
  first line, role and dates italic on the second, then ringed bullets.
- The grade as `\cventry`'s fifth argument, which the banking style prints on
  the end of the degree line rather than on one of its own. Worth naming
  because the obvious reading of the template puts it on its own line and that
  is wrong.
- `\cvitem` inline with a bold label, which is what banking does with it
  instead of the hints column the other styles use.
- The `n/m` page footer.

## Differences from the LaTeX render

- **More compact.** On a real resume the Tectonic render runs to three pages and
  this one to two. Same content, same order.
- **No hyphenation**, so long lines wrap rather than breaking words.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of `services/typst_render.py`.
