# Jake's Resume

- Upstream: https://github.com/jakegut/resume (`resume.tex`), archived August 2024.
- Author: Jake Gutierrez.
- Licence: MIT. `LICENSE` in this directory is the upstream file, unchanged.
- Vendored here: nothing beyond the licence. The template needs no class file
  and no fonts; every package it loads comes from Tectonic's bundle.

## Changes from upstream

`template.tex.j2` keeps the upstream preamble, macros and spacing. Two changes:

1. The body is driven by placeholders instead of Jake's own resume content.
2. `\input{glyphtounicode}` and `\pdfgentounicode=1` are guarded by
   `\ifPDFTeX`. Both are pdfTeX-only, and Tectonic is XeTeX, where the first
   raises `Undefined control sequence: \pdfglyphtounicode` and nothing
   compiles. Under XeTeX, xdvipdfmx writes the ToUnicode map itself, so text
   still extracts; this was verified, not assumed.
