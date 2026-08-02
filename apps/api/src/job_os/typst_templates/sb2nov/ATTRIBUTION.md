# sb2nov resume, Typst port

- Upstream: https://github.com/sb2nov/resume (`sourabh_bajaj_resume.tex`).
- Author: Sourabh Bajaj.
- Licence: MIT. `LICENSE` in this directory is the upstream file, unchanged.
  The upstream README notes that the format is MIT but that the data in the
  example is the author's own, so none of his content is reused here.
- Vendored here: nothing beyond the licence. The type is New Computer Modern,
  which Typst embeds, so this template needs no font files at all.

This is a port, not a translation. The LaTeX original is
`latex_templates/sb2nov/template.tex.j2` and remains the reference: where the
two disagree, that one is right and this one has a bug.

## What was reproduced

US Letter with half-inch margins and Computer Modern at 11pt, shared with Jake's
because the two share a lineage. The header is a two-column block, name and
links down the left and the ways to reach this person down the right, including
the optional third row this repository added so profile links have somewhere to
go. Section headings are small caps over a rule.

The marker scheme is the detail that distinguishes this template from Jake's and
it is reproduced exactly: entries are themselves bulleted, because the LaTeX
list is opened with `leftmargin=*` and keeps its markers, and the detail beneath
each entry is a nested ring. Jake's hides both.

## Differences from the LaTeX render

- **Slightly tighter vertically**, by roughly the same margin as Jake's. The two
  engines space lists by different rules, and closing that gap exactly would
  mean hard-coding spacing that only holds for one document.
- **The en dash is a real character** rather than LaTeX's `--` ligature. The
  printed glyph is the same.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of `services/typst_render.py`.

One Typst parsing trap is worth recording, because it cost a debugging round and
looks like nothing: a method chain broken across lines ends at the first
complete expression, so

    #let x = d.profiles
      .map(..)

binds `x` to the bare array and silently drops the rest. Wrap the whole chain in
parentheses.
