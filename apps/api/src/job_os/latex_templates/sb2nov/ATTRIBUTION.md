# sb2nov resume

- Upstream: https://github.com/sb2nov/resume (`sourabh_bajaj_resume.tex`).
- Author: Sourabh Bajaj.
- Licence: MIT. `LICENSE` in this directory is the upstream file, unchanged.
  The upstream README notes that the format is MIT but the data in the example
  is the author's own, so none of his content is reused here.
- Vendored here: nothing beyond the licence. No class file, no fonts.

## Changes from upstream

1. The body is driven by placeholders instead of the author's resume content.
2. `\input{glyphtounicode}` and `\pdfgentounicode=1` are guarded by
   `\ifPDFTeX`, for the same reason as Jake's: both are pdfTeX-only and
   Tectonic is XeTeX.
3. The header table gained an optional third row, so profile links have
   somewhere to go when a resume has more of them than the original two-line
   header could hold.
