# Vendored fonts

Faces used by more than one Typst template, kept here once rather than copied
into each. Underscored so the name cannot be mistaken for a template key:
`typst_render.builtin_directory` only accepts keys matching `[a-z0-9][a-z0-9-]*`,
so nothing can reach this directory through one.

Every file was taken from the source the LaTeX side already renders from, so the
two engines set the same design in the same faces rather than in two things that
look similar. All three permit commercial use and redistribution.

| directory | family as Typst resolves it | licence | source |
| --- | --- | --- | --- |
| `sourcesanspro/` | Source Sans Pro | SIL OFL 1.1 | Adobe, [source-sans 3.006R](https://github.com/adobe-fonts/source-sans/releases/tag/3.006R) |
| `roboto/` | Roboto, Roboto Slab | Apache-2.0 | CTAN [`roboto`](https://ctan.org/pkg/roboto) |
| `fontawesome5/` | Font Awesome 5 Free Solid, Font Awesome 5 Brands | SIL OFL 1.1 (fonts) | CTAN [`fontawesome5`](https://ctan.org/pkg/fontawesome5) |

Notes on each:

- **Source Sans Pro** is taken at 3.006R deliberately: that is the last Adobe
  release under the Pro name, and it is the design TeX Live's `sourcesanspro`
  package ships, which is what Awesome-CV renders with today. Later releases are
  named Source Sans 3 and are a revised design. Only the six faces the template
  uses are vendored, not the full family. `LICENSE.md` is Adobe's file, unchanged.
- **Roboto and Roboto Slab** come from the CTAN package rather than Google Fonts,
  for the same reason: the CTAN `roboto` package is where Tectonic's bundle gets
  them, so these are byte-for-byte the faces the LaTeX renders use.
  `LICENSE.txt` is the package's own Apache-2.0 text.
- **Font Awesome 5 Free.** The CTAN package ships no licence file, so
  `LICENSE.txt` was fetched from the Font Awesome repository itself. The icons
  as SVG are CC BY 4.0 and the fonts are OFL 1.1; only the fonts are here.
  `README.md` is the CTAN package's, kept because it is where the OFL statement
  for these files is written down.

Lato and Raleway are NOT here. They were already vendored at
`latex_templates/deedy/fonts/`, with the OFL text upstream omits, and the
renderer adds that directory to the font path instead of holding a second copy.

## A missing face fails silently, so it is asserted

This is the trap this directory exists to avoid. Typst does not fail a compile
when a family cannot be found: it substitutes another and carries on, so the
resume renders, looks wrong, and nobody notices until it is in front of an
employer. Filenames do not help either, since a family resolves by the name
inside the file and not the name of the file: Lato ships as `Lato-Reg.ttf` and
resolves as `Lato`.

So `typst_render.FONT_REQUIREMENTS` records the families each template names,
`missing_fonts` asks the binary which ones it can actually see, and both the
test suite and the image build fail if any is absent. Adding a template that
names a new face means adding it there too.
