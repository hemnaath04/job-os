# Awesome-CV

- Upstream: https://github.com/posquit0/Awesome-CV (`awesome-cv.cls`).
- Author: Claud D. Park (posquit0).
- Licence: LPPL v1.3c for the class. `LICENCE` in this directory is the upstream
  file, unchanged.
- Vendored here: `awesome-cv-tectonic.cls`. No fonts: upstream removed its
  bundled copies in favour of the ones a TeX distribution provides, and
  Tectonic's bundle has them.

## Why the class is renamed

LPPL 1.3c requires a modified copy to be distributed under a different file
name. `awesome-cv.cls` became `awesome-cv-tectonic.cls`, and its
`\ProvidesClass` line matches. Three changes, all marked `CHANGED` in the file:

1. `\RequirePackage{fontawesome6}` became `\RequirePackage{fontawesome5}`.
   Tectonic's bundle is TeX Live 2022, which has no `fontawesome6.sty`, so the
   class would not load at all. The six Font Awesome 6 macro names the class
   calls (`\faHouseChimney`, `\faSquareGithub`, `\faSquareWhatsapp`,
   `\faSquareXing`, `\faCakeCandles`, `\faXTwitter`) are aliased onto their
   v5 equivalents with `\providecommand`, so a future bundle that does carry v6
   takes precedence over the aliases.
2. `\setmainfont{Source Sans 3}`, `\setsansfont{Source Sans 3}` and
   `\newfontfamily\roboto{Roboto}` now name font files
   (`SourceSansPro-*.otf`, `Roboto-*.otf`) rather than families. Tectonic
   resolves a bare family name through the host's font system, which in a
   container has neither font installed; a file name is served from the bundle.
   Source Sans Pro is the older release of the same typeface, which is what the
   TeX Live 2022 bundle carries.
3. `Renderer=HarfBuzz` is dropped. It is LuaTeX-only and produced a warning on
   every run.

## The template file

`template.tex.j2` was written against the class's documented commands, not
copied from `examples/resume.tex`. The upstream examples are CC BY-SA 4.0, whose
ShareAlike term would reach any derivative of them; the class itself is LPPL and
carries no such term.
