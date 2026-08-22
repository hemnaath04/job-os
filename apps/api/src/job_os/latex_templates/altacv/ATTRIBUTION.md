# AltaCV

- Upstream: https://github.com/liantze/AltaCV (`altacv.cls` v1.7.4, 30 Jul 2025).
- Author: LianTze Lim.
- Licence: LPPL 1.3 or later. `LICENSE.md` in this directory is the upstream
  file, unchanged.
- Vendored here: `altacv-tectonic.cls`, and (see below) Lato. Roboto Slab
  still ships inside Tectonic's bundle and needs nothing local.

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

## Lato is vendored here, not left to Tectonic's bundle

This directory originally carried no font files at all, on the assumption
that Tectonic's own remote bundle would resolve `Lato-Regular.ttf` and its
three weights by that exact filename the way it resolves `RobotoSlab-*.otf`.
That held until a production image build failed outright on
`Lato-BoldItalic.ttf` specifically -- "the font ... cannot be found" -- which
is a build-time network dependency on a resource this repository already
has a licensed copy of one directory over. `deedy/fonts/lato/` vendors the
same family (abbreviated filenames: `Lato-Reg.ttf`, `Lato-Bol.ttf`,
`Lato-RegIta.ttf`, `Lato-BolIta.ttf`) for its own template; the four files
here are copies of those, renamed to the full-word filenames this template's
`\setsansfont` call already named, plus `OFL.txt` alongside them. Not a
symlink or a build-time copy: `compile_pdf`'s asset step only copies files
that already live in a template's own directory, so a shared source would
need its own cross-template plumbing this codebase does not have. Two copies
of the same four files, under the same open licence, is the honest cost of
that.
