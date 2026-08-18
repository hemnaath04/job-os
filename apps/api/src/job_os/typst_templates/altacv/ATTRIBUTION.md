# AltaCV, Typst port

- Upstream: https://github.com/liantze/AltaCV.
- Author: LianTze Lim.
- Licence: LPPL-1.3 or later. `LICENSE.md` in this directory is the upstream
  file, unchanged.
- Fonts: Lato (SIL OFL 1.1), from the copies vendored at
  `latex_templates/deedy/fonts/`, and Font Awesome 5 Free and Brands
  (SIL OFL 1.1) from `typst_templates/_fonts/`.

The reference is `latex_templates/altacv/altacv-tectonic.cls` together with the
colour scheme the LaTeX template sets, which is the upstream sample's.

## What was reproduced

- `\geometry{left=1.25cm,right=1.25cm,top=1.5cm,bottom=1.5cm,columnsep=1.1cm}`
  and `\columnratio{0.62}`, so the history sits in the wide column and the short
  scannable material in the sidebar.
- The palette: VividPurple `#3E0097` for the tagline, headings, heading rules
  and accents, SlateGrey `#2E2E2E` for emphasis, LightGrey `#666666` for body.
- Section titles uppercased in purple over a purple rule.
- `\cvevent` as the role, the organisation in purple bold, then a date and place
  line carrying the class's calendar and marker icons.
- `\divider`, the dashed grey rule between events.
- `\cvtag`, the rounded outline pills that are the sidebar's whole look, and
  `\cvachievement`, an icon in the margin against a bold line and a quieter one.
- The header contact order. The LaTeX template calls `\email`, `\phone`,
  `\location` and then the remaining links in that order, so this sorts the
  render model's contact list to match rather than printing it in the order the
  model happens to build it.

One deliberate carry-over rather than a fix: in the sidebar, a `\cvevent`'s date
and location are two half-width boxes that collide in a column this narrow, so
education entries put the place on its own line underneath. The LaTeX template
does the same thing for the same reason.

Note that although the class loads Roboto Slab as the main font, it then sets
`\familydefault` to the sans, so everything visible on the page is Lato. This
port uses Lato throughout, which is what the LaTeX render actually shows. Roboto
Slab is still vendored, because the LaTeX class still loads it.

## Differences from the LaTeX render

- **The text layer is badly broken in the LaTeX version and correct here, and on
  this template that is the headline difference.** Extracting text from the
  Tectonic render yields raw macro names: `\faGlobe : https://example.com/jordan`
  and `\faPreselectedIcon {certificate}: Sample Cloud Practitioner`. The class
  writes accessibility text through `accsupp` and, under this engine, some of it
  comes out as the unexpanded macro. An applicant tracking system reading that
  page sees `\faPreselectedIcon {certificate}` as literal text next to the
  certification. This template is already the second riskiest of the seven for
  automated parsing; that makes it worse in a way nobody would find by looking.
- **No hyphenation and no justification.** The LaTeX render justifies and
  hyphenates, producing "indepen-dently" and "ex-ception" broken across lines.
  Those hyphens are in the text layer too.
- **More compact**, in the same way as the other ports, so the sidebar and the
  wide column both end higher up the page.

## Placeholders

There are none, and no template language. The renderer writes the document as
`data.json` beside this file and the template reads it with `json()`. A value
interpolated in Typst code mode is never re-parsed as markup, which is why
nothing here is escaped. See the note at the top of `services/typst_render.py`.
