# husky

Not a port of a public repository. This reproduces, field for field, a
personal resume `.tex` file the user (a Northeastern Khoury co-op applicant)
supplied directly as "the proper format" for their own co-op and internship
applications, after finding job.os's other single-column templates put
location last in the contact line and lost line-spacing/heading fidelity
against it. The preamble, macros and section order below are copied from that
file exactly; only the literal resume content was replaced with the
`build_render_model` placeholders every builtin template uses.

- Upstream: none. The reference file is the user's own personal document, not
  a published template; there is no LICENSE file in this directory because
  there is no upstream licence to carry.
- Author: the layout is the user's; the Jinja port for job.os was written here.
- Fonts: none vendored. `mathptmx` swaps the article class's default Computer
  Modern for Times-like PostScript fonts through classical NFSS, which needs
  no `\fontspec` call and no font files, so it costs nothing new in Tectonic's
  package cache.

## What distinguishes this from the other single-column templates

1. **Times (mathptmx), not Computer Modern.** jakes, sb2nov and dashline all
   use the article class's default serif. This is the only bundled template
   that swaps it.
2. **A two-column `\entry` line, not a one-line or two-line header.** Title
   and italic subtitle stack on the left; date (and an optional second
   right-hand line) stack on the right, via two `minipage`s rather than
   `tabularx`. Matches the reference file's own macro of the same name.
3. **A dedicated `\skillrow` macro**, one bold-italic label per line rather
   than a single bulleted skills block.
4. **Tighter geometry**: 0.45in/0.4in/0.6in/0.6in margins (top/bottom/left/
   right) instead of the half-inch-all-around trick jakes/sb2nov/dashline use,
   and `\parskip` of 1pt rather than relying on itemize spacing alone.

No typst_ready flag. Typst's only embedded serif that is not New Computer
Modern is Libertinus Serif, which is a different design from Times and would
not honestly match this template's LaTeX render, so this renders through
Tectonic only -- the same choice moderncv already makes for the same reason
(no port until one has actually been checked side by side and matches).

## Placeholders

Same Jinja-with-LaTeX-delimiters contract as the other seven bundled LaTeX
templates; see `latex_templates/README.md` and `latex_render.build_render_model`
for the full field list. Nothing here reaches an unescaped value.
