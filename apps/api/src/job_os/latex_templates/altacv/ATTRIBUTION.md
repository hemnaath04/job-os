# AltaCV

- Upstream: https://github.com/liantze/AltaCV (`altacv.cls` v1.7.4, 30 Jul 2025).
- Author: LianTze Lim.
- Licence: LPPL 1.3 or later. `LICENSE.md` in this directory is the upstream
  file, unchanged.
- Vendored here: `altacv-tectonic.cls`. No fonts: Roboto Slab and Lato both
  ship inside Tectonic's bundle.

## Why the class is renamed

LPPL requires a modified copy to be distributed under a different file name.
`altacv.cls` became `altacv-tectonic.cls`, with a matching `\ProvidesClass`.
One change, marked `CHANGED` in the file:

`\RequirePackage[a-1b]{pdfx}` became `\RequirePackage{hyperref}`.

pdfx cannot work under Tectonic. It mints its PDF/A creation timestamp by
shelling out to `texlua`, which Tectonic does not ship, and without shell escape
it raises `Package pdfx Error: CreationDate is not properly supported`. There is
a known workaround (`\let\pdfcreationdate=\creationdate` before
`\documentclass`) and it does compile, but the XMP packet that comes out makes
xdvipdfmx report `Could not find any valid object`, and there are reports of
Acrobat then refusing to open the file. A resume that some readers cannot open
is worse than a resume without PDF/A metadata, so PDF/A is dropped. hyperref is
the only thing this class actually used pdfx for.

## Changes in the template file

`template.tex.j2` follows the upstream sample's structure and colour scheme,
with two deliberate differences:

1. Fonts are named by file (`RobotoSlab-Regular.otf`, `Lato-Regular.ttf`)
   because Tectonic cannot look a family name up in a container.
2. Education entries in the sidebar pass an empty location to `\cvevent` and
   print the location on its own line. `\cvevent` lays date and location out as
   two half-width boxes, which overlap in a column this narrow.
