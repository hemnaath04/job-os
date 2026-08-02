"""Render every ported Typst template once, so a broken one fails the build.

The counterpart to `warm_latex_cache`, minus the warming. Typst has no package
cache to fill: it embeds its own fonts, the bundled templates import nothing, and
the two that need extra faces read them from the font files vendored in this
repository. Nothing is fetched at render time, so there is nothing to fetch at
build time either.

What is left is the half of `warm_latex_cache` that actually earns its place in
the image build: compiling each template proves it still compiles, on the same
architecture and against the same binary that will serve requests, rather than
discovering it on somebody's render.

Also renders through the sparsest document that is still valid, because every
template has to survive a resume with no projects, no awards and no summary, and
that is exactly the case a template author forgets.

    python -m job_os.scripts.check_typst_templates
"""
from __future__ import annotations

import sys
import time

from job_os.services.latex_catalog import BUILTIN_TEMPLATES, SAMPLE_RESUME
from job_os.services.typst_render import (
    TypstRenderError,
    has_builtin,
    missing_fonts,
    render_resume_pdf,
    typst_binary,
)

# A resume with nothing optional filled in. Not a realistic document, which is
# the point: it is the shape that finds the section a template forgot to guard.
SPARSE_RESUME: dict = {
    "basics": {"name": "Sparse Sample", "email": "sparse@example.com"},
    "work": [],
    "education": [],
    "projects": [],
    "skills": [],
}


def main() -> int:
    binary = typst_binary()
    if binary is None:
        print("No typst binary on PATH.", file=sys.stderr)
        return 2
    print(f"typst: {binary}")

    ported = [spec for spec in BUILTIN_TEMPLATES if has_builtin(spec.key)]
    if not ported:
        print("No Typst templates found.", file=sys.stderr)
        return 2

    failures: list[str] = []
    for spec in ported:
        # Checked before rendering, because a missing face does not fail a
        # compile. Typst substitutes one silently and the page comes out wrong,
        # which is invisible here and very visible to whoever opens the PDF.
        absent = missing_fonts(spec.key)
        if absent:
            failures.append(f"{spec.key} (fonts)")
            print(
                f"FAIL {spec.key}: these faces did not resolve: {', '.join(absent)}",
                file=sys.stderr,
            )
            continue

        for label, document in (("sample", SAMPLE_RESUME), ("sparse", SPARSE_RESUME)):
            started = time.monotonic()
            try:
                pdf = render_resume_pdf(document, template_key=spec.key).bytes_
            except TypstRenderError as exc:
                failures.append(f"{spec.key} ({label})")
                print(f"FAIL {spec.key} [{label}]: {exc}\n{exc.log}", file=sys.stderr)
                continue
            if not pdf.startswith(b"%PDF"):
                failures.append(f"{spec.key} ({label})")
                print(f"FAIL {spec.key} [{label}]: not a PDF", file=sys.stderr)
                continue
            seconds = time.monotonic() - started
            flag = "" if spec.typst_ready else "  (not yet enabled)"
            print(f"ok   {spec.key:<12} {label:<7} {len(pdf):>7} bytes  {seconds:6.3f}s{flag}")

    skipped = [spec.key for spec in BUILTIN_TEMPLATES if not has_builtin(spec.key)]
    if skipped:
        print(f"\nStill LaTeX only: {', '.join(skipped)}")

    if failures:
        print(f"\n{len(failures)} render(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\nAll {len(ported)} Typst template(s) rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
