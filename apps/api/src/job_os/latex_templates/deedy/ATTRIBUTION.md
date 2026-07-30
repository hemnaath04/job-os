# Deedy

- Upstream: https://github.com/deedy/Deedy-Resume, the `OpenFonts` variant.
- Author: Debarghya Das.
- Licence: Apache License 2.0, taken from the upstream repository. `LICENSE` in
  this directory is that file, unchanged. Note that Deedy-derived templates
  republished on latextemplates.com carry CC BY-NC-SA instead; this copy is not
  from there, and the Apache-2.0 chain is the repository's own.
- Vendored here: `deedy-resume-openfont.cls` unchanged, plus the ten Lato and
  nine Raleway faces the class loads by relative path. The class cannot use
  CTAN's Lato instead: it asks for `Lato-Lig`, `Lato-Hai` and `Lato-Reg`, which
  are the older Lato 1 file names and do not exist in the CTAN package.

## Fonts

Lato is SIL OFL 1.1 (tyPoland, Lukasz Dziedzic) and Raleway is SIL OFL 1.1
(Matt McInerney, Pablo Impallari, Rodrigo Fuenzalida). The upstream repository
ships neither font's licence text, which OFL 1.1 section 2 requires of a
redistribution, so `fonts/lato/OFL.txt` and `fonts/raleway/OFL.txt` were added
here from each font's own source.

The `MacFonts` variant of this template is deliberately not used: it asks for
`Helvetica Neue Light` and `Heiti TC Medium` by family name, which exist only on
macOS.

## Changes in the template file

The class is unmodified. `template.tex.j2` differs from the upstream example in
two ways beyond being placeholder-driven:

1. `\defaultfontfeatures{Ligatures=TeX}` plus a reissued `\setmainfont`. The
   class loads its faces without TeX ligatures, so every date range printed as
   two hyphens instead of a dash.
2. `\vspace{\topsep}` before the first bulleted list in a column. `tightemize`
   opens with a negative skip that collides with the line above it when the list
   is the first in a column; the upstream example compensates the same way, and
   calls it out as a known issue.

Upstream's other known issue is still true: a column whose contents exceed the
page height runs onto a second page.
