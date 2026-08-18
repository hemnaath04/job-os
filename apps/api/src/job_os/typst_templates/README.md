# Bundled Typst resume templates

The fast path beside `latex_templates/`. Same seven designs, same contract, a
different engine. Six are ported; moderncv still renders through Tectonic, and
the app is none the wiser either way.

| template | engine | fonts |
| --- | --- | --- |
| jakes | Typst | New Computer Modern, embedded in Typst |
| sb2nov | Typst | New Computer Modern, embedded in Typst |
| deedy | Typst | Lato, Raleway, from `latex_templates/deedy/fonts/` |
| awesome-cv | Typst | Source Sans Pro, Roboto, Font Awesome 5 |
| altacv | Typst | Lato, Font Awesome 5 |
| dashline | Typst | New Computer Modern, embedded in Typst |
| moderncv | Typst, not enabled | Latin Modern Sans |

Six are ported. Which of them a deployment actually uses is a separate
decision, recorded as `typst_ready` in `latex_catalog.py`; today that is every
ported template. moderncv has no port at all and renders on either engine and
is waiting on somebody looking at the two pages side by side.

moderncv is the one to be careful with, and its ATTRIBUTION.md says why at
length: it is the only template with no class file in this repository to measure
against, so the port is a reconstruction from rendered output rather than a
transcription. It came out closer than expected, but a change upstream would
show up in the Tectonic render and not here, and nothing local would catch it.

Rendering is `services/typst_render.py`. Whether a request reaches it at all is
decided by `RENDER_ENGINE` plus the per-template `typst_ready` flag in
`latex_catalog.py`; Tectonic is the default and the fallback.

## Why this is here

Tectonic renders a resume in about twenty seconds. Typst renders the same page
in about a hundredth of one, measured on the same document and the same machine.
Nothing else about LaTeX was found wanting, and nothing about it is being
removed: `latex_render.py` is still the default, still the fallback, and still
the only engine that compiles a template written by a model from somebody's
upload, because that is where the hardening for executing untrusted markup
lives.

## A directory holds

- `resume.typ` - the whole template. One file, no includes, no packages.
- the upstream licence, kept as it was received.
- `ATTRIBUTION.md` - where the design came from, what was measured off the
  original, and every way this port differs from the LaTeX render. The
  differences section is the honest part and is meant to stay honest.

Fonts are not duplicated here. Deedy and AltaCV read theirs from
`latex_templates/deedy/fonts/`, which the renderer adds to Typst's font path.

## There is no template language, and no escaping

The renderer writes the document as `data.json` in the compile directory and the
template reads it with `json()`. There is no Jinja layer, so there is no
equivalent of the LaTeX side's trap where a `%` comment fails to hide a
placeholder.

More importantly there is no escape table, and adding one would be a mistake.
A LaTeX template pastes a resume value into source the engine then parses as
instructions, which is why every value has to be escaped on the way in. A Typst
template never does: the value is interpolated in code mode, and a string
interpolated in code mode is never re-parsed as markup. Feeding
`#set page(width:1pt)`, `#read("/etc/passwd")`, `*bold*` and `$x^2$` through as
resume fields prints those characters and executes none of them. That is
asserted against a real compile in `tests/test_typst_render.py`, because the
decision not to escape rests entirely on it.

What `sanitize` does is not escaping. It strips control and zero-width
characters, which would corrupt the JSON or the text layer, and flattens a
newline inside a field to a space, which would otherwise end the enclosing list
item. Unicode typography is left exactly as the candidate typed it, because
Typst has the glyphs and LaTeX's transliteration table does not apply.

## The engine constrains what these can do

1. **No package imports.** `@preview` is the one thing in Typst that reaches the
   network, and 0.15.1 has no flag to forbid it, so `typst_render` refuses any
   source containing one. Every template here is self-contained.
2. **Fonts are files.** Renders run with `--ignore-system-fonts`, so a template
   sees only what Typst embeds (New Computer Modern, Libertinus Serif, DejaVu
   Sans Mono) and what is on the font path. Without that flag a template would
   silently resolve a face on a developer's machine that a container cannot,
   and the render that was checked would not be the render that ships.
3. **The compile directory is the root.** `--root` is the scratch directory, so
   an absolute path resolves inside it and a `..` path is refused outright.

## Traps met while porting, all silent

Recorded because each one produced output that looked plausible rather than an
error:

- A method chain broken across lines ends at the first complete expression:
  `#let x = d.profiles` followed by an indented `.map(..)` binds `x` to the bare
  array. Parenthesise the chain.
- A bare `[ ]` between two `text()` calls in code mode collapses to nothing,
  which ran a name together as "JordanA. Sample". Use `h(0.4em)`.
- `\:` is not an escape, so `[#name\://]` will not parse. Build the string in
  code mode.
- Typst does not inherit a LaTeX class's fixed baseline skips. Where a class
  sets 16pt type on a 24pt skip, the leading has to be asked for explicitly or
  a heading that wraps opens up much wider than the original.

## A missing font is the trap, and it is asserted

Typst does not fail a compile when a family is absent: it substitutes another
and carries on, so the page renders, looks wrong, and nobody notices. So
`typst_render.FONT_REQUIREMENTS` records the families each template names,
`missing_fonts` asks the binary which ones it can actually resolve, and the test
suite and the image build both fail if any is missing. A template that starts
using a new face has to be added there too.
