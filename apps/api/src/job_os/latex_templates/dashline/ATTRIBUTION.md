# dashline

Not a port. This is an original job.os template, written from scratch for this
repository, reproducing a layout that is extremely common in early-career /
new-grad software resumes (the style widely shared on r/EngineeringResumes and
similar communities): a centred plain header, section headings underlined by a
rule with no small caps, entries whose title/employer/place sit on one bold
line with the date range flush right, and en-dash bullets rather than round
ones.

- Upstream: none. No single named repository was copied or adapted; there is
  no LICENSE file in this directory because there is no upstream licence to
  carry.
- Author: written for job.os.
- Fonts: none vendored. Uses Tectonic's default article-class serif (Latin
  Modern), the same choice `jakes` and `sb2nov` make, so it needs no class
  file and nothing new in Tectonic's package cache beyond what those two
  already warm.

## Where the shape came from

Modelled on a real single-column resume read while building this template
(not reproduced here, and no identifying content from it appears in this
template or its sample data). Three structural traits distinguish it from
`jakes`/`sb2nov`, which is why it exists as its own template rather than a
tweak to one of them:

1. **One header line per entry, not two.** Jake's splits an entry across two
   lines (title + date, then employer + place, italicised). This template
   puts company, role and location on a single bold line with the date range
   flush right, and drops straight into bullets -- no second header line.
2. **En-dash bullets, not round ones.** `\itemsStart` sets
   `label={\textendash}` instead of enumitem's default disc.
3. **Plain-case section headings.** `\titleformat{\section}` here is
   `\bfseries`, not `\scshape`. Jake's headings read EDUCATION in small caps;
   this template's read Education.

Two things were read directly off the source PDF rather than assumed, because
the two disagreed with a first guess:

- The education entry's degree/concentration line renders in **regular
  weight, not italic**, even though that is the conventional choice for this
  slot in most templates (jakes italicises the equivalent line). Checked
  against a rendered page, not the raw text extraction, since regular vs.
  italic does not show up in extracted text.
- The project entry's tech-stack line **is** italic, and sits on its own line
  below the name + date row, rather than inline with the name the way jakes
  does it.

## Placeholders

Same Jinja-with-LaTeX-delimiters contract as the other five bundled LaTeX
templates; see `latex_templates/README.md` and `latex_render.build_render_model`
for the full field list. Nothing here reaches an unescaped value.
