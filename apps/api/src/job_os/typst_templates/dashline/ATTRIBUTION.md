# dashline, Typst port

- Upstream: none, same as the LaTeX original at
  `latex_templates/dashline/template.tex.j2`; see that file's ATTRIBUTION.md
  for what the layout reproduces and what was checked against a rendered page.
- Vendored here: nothing. The type is New Computer Modern, which Typst
  embeds, so this template needs no font files.

This is a port, not a translation. The LaTeX original is the reference: where
the two disagree, that one is right and this one has a bug.

## What was reproduced

US Letter with half-inch margins, Computer Modern at 11pt, no hyphenation, a
centred bold name over a bar-separated contact line, plain bold (not
small-caps) section headings underlined by a rule, and entries that put
title/employer/place on one bold line with the date flush right rather than
splitting them across two lines the way jakes/sb2nov do.

## Differences from the LaTeX render

- **The en dash is a real character**, same reasoning as the jakes port: LaTeX
  turns `--` into an en dash through a ligature and this template's bullet
  marker instead sets `marker: [–]` directly, which every face bundled with
  Typst can render.
- **No hyperlink underline anywhere**, including the header. The LaTeX
  original does not underline its header links either (that convention is
  `jakes`' own, not this template's), so there is nothing to reconcile here.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. See the
note at the top of `services/typst_render.py` for why nothing here needs
escaping.
