# ModernCV

- Upstream: https://ctan.org/pkg/moderncv, maintained at
  https://github.com/moderncv/moderncv.
- Authors: Xavier Danaux (2006-2015) and the moderncv maintainers (2020-).
- Licence: LPPL-1.3c.
- Vendored here: nothing. `moderncv.cls`, its styles and its colour files all
  ship inside Tectonic's own package bundle, so there is no copy of the class in
  this repository to license or to keep up to date.

## Notes

- The bundle is TeX Live 2022, so the styles available are `banking`, `casual`,
  `classic`, `empty`, `fancy` and `oldstyle`. The `contemporary` style and the
  `cerulean` colour that appear in the current upstream `template.tex` are
  newer than the bundle and fail with a missing-file error. This template uses
  `banking` with `blue`.
- `\moderncvicons{letters}` replaces the default Font Awesome icon set. Under
  Tectonic the Font Awesome faces come out with no usable ToUnicode map, so a
  parser reading the contact line got a run of junk characters where the icons
  are. The letter icons are ordinary glyphs and extract as text.
- Tectonic must be 0.17.0 or newer. Versions 0.15.0 through 0.16.9 abort with
  `free(): invalid pointer` while loading moderncv's Font Awesome helper
  (tectonic issues 1342 and 1366, fixed in 0.17.0).
