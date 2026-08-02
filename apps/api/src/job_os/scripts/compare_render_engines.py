"""Render the same document through Tectonic and Typst, and put the two side by side.

The evidence for a cutover. A render-time number on its own is not a reason to
move a template: the whole point of a resume template is how the page looks, and
a port that renders in a tenth of the time but sets the dates in the wrong place
is a regression that happens to be fast. So this produces both halves, the times
and the pages, for whoever has to make the call.

Writes, per template, into the output directory:

    <key>-tectonic.pdf   <key>-tectonic.png
    <key>-typst.pdf      <key>-typst.png

and prints a table of wall-clock times. The PDFs are the honest artefact, since
a PDF is what an employer opens; the PNGs exist so two pages can be put next to
each other without a reader.

    python -m job_os.scripts.compare_render_engines
    python -m job_os.scripts.compare_render_engines --out /tmp/compare
    python -m job_os.scripts.compare_render_engines --resume my-resume.json
    python -m job_os.scripts.compare_render_engines --only jakes,deedy

By default it renders the bundled sample, which is invented on purpose so a
shared artefact never carries somebody's real phone number. `--resume` takes a
JSON Resume document when the comparison needs to be against real content, which
is the better test: real resumes are longer, and length is exactly where two
engines' spacing rules diverge.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from job_os.scripts.seed_latex_templates import rasterize_first_page
from job_os.services import typst_render
from job_os.services.latex_catalog import BUILTIN_TEMPLATES, SAMPLE_RESUME
from job_os.services.latex_render import (
    LatexRenderError,
    build_render_model,
    builtin_directory,
    compile_pdf,
    fill_template,
    load_builtin_source,
    tectonic_binary,
)


def _render_tectonic(key: str, document: dict[str, Any]) -> tuple[float, bytes]:
    started = time.perf_counter()
    pdf = compile_pdf(
        fill_template(load_builtin_source(key), build_render_model(document)),
        assets_dir=builtin_directory(key),
    )
    return time.perf_counter() - started, pdf


def _render_typst(key: str, document: dict[str, Any]) -> tuple[float, bytes]:
    started = time.perf_counter()
    pdf = typst_render.render_resume_pdf(document, template_key=key).bytes_
    return time.perf_counter() - started, pdf


def _write(out: Path, key: str, engine: str, pdf: bytes) -> None:
    (out / f"{key}-{engine}.pdf").write_bytes(pdf)
    png = rasterize_first_page(pdf)
    if png:
        (out / f"{key}-{engine}.png").write_bytes(png)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("render-comparison"))
    parser.add_argument("--resume", type=Path, help="a JSON Resume document to render")
    parser.add_argument("--only", help="comma-separated template keys")
    parser.add_argument(
        "--all",
        action="store_true",
        help="include templates with no Typst port, rendering only through Tectonic",
    )
    args = parser.parse_args()

    if tectonic_binary() is None:
        print("No tectonic binary on PATH, so there is nothing to compare to.", file=sys.stderr)
        return 2
    if typst_render.typst_binary() is None:
        print("No typst binary on PATH.", file=sys.stderr)
        return 2

    document = SAMPLE_RESUME
    source_label = "the bundled sample"
    if args.resume:
        document = json.loads(args.resume.read_text())
        source_label = str(args.resume)

    wanted = {key.strip() for key in args.only.split(",")} if args.only else None
    specs = [
        spec
        for spec in BUILTIN_TEMPLATES
        if (wanted is None or spec.key in wanted)
        and (args.all or typst_render.has_builtin(spec.key))
    ]
    if not specs:
        print("No templates selected.", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"document: {source_label}")
    print(f"output:   {args.out.resolve()}\n")
    print(f"{'template':<12} {'tectonic':>10} {'typst':>10} {'speedup':>9} {'ported':>8}")
    print("-" * 54)

    failures: list[str] = []
    for spec in specs:
        try:
            tectonic_seconds, tectonic_pdf = _render_tectonic(spec.key, document)
            _write(args.out, spec.key, "tectonic", tectonic_pdf)
        except LatexRenderError as exc:
            failures.append(f"{spec.key} (tectonic)")
            print(f"{spec.key:<12} FAILED: {exc}", file=sys.stderr)
            continue

        if not typst_render.has_builtin(spec.key):
            print(f"{spec.key:<12} {tectonic_seconds:9.2f}s {'-':>10} {'-':>9} {'no':>8}")
            continue

        try:
            typst_seconds, typst_pdf = _render_typst(spec.key, document)
            _write(args.out, spec.key, "typst", typst_pdf)
        except typst_render.TypstRenderError as exc:
            failures.append(f"{spec.key} (typst)")
            print(f"{spec.key:<12} FAILED: {exc}\n{exc.log}", file=sys.stderr)
            continue

        # Marked "no" when the port exists but has not been signed off, so the
        # table never implies a template is live when it is not.
        print(
            f"{spec.key:<12} {tectonic_seconds:9.2f}s {typst_seconds:9.3f}s "
            f"{tectonic_seconds / typst_seconds:8.0f}x {'yes' if spec.typst_ready else 'no':>8}"
        )

    if failures:
        print(f"\n{len(failures)} render(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\nOpen the PDFs in pairs. A template only moves if the pages match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
