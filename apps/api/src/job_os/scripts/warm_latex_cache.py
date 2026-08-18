"""Compile every bundled template once, to fill Tectonic's package cache.

Run at image build time, when the network is still reachable. Tectonic fetches
LaTeX packages, fonts and its format file from a remote bundle the first time a
document needs them, so a container that has never compiled anything would go to
the network on a user's first render, and would fail outright if that render
happened to be offline. Compiling all six here lands every file they need in
TECTONIC_CACHE_DIR, which is baked into the image, and lets requests render with
`--only-cached`.

It doubles as the build's own check that the seven templates still compile: a
failure here fails the image, rather than shipping a template that cannot
render.

    python -m job_os.scripts.warm_latex_cache
"""
from __future__ import annotations

import os
import sys
import time

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


def main() -> int:
    binary = tectonic_binary()
    if binary is None:
        print("No tectonic binary on PATH.", file=sys.stderr)
        return 2
    cache = os.environ.get("TECTONIC_CACHE_DIR") or "(tectonic's default)"
    print(f"tectonic: {binary}\ncache:    {cache}")

    model = build_render_model(SAMPLE_RESUME)
    failures: list[str] = []
    for spec in BUILTIN_TEMPLATES:
        started = time.monotonic()
        try:
            pdf = compile_pdf(
                fill_template(load_builtin_source(spec.key), model),
                assets_dir=builtin_directory(spec.key),
            )
        except LatexRenderError as exc:
            failures.append(spec.key)
            print(f"FAIL {spec.key}: {exc}\n{exc.log}", file=sys.stderr)
            continue
        seconds = time.monotonic() - started
        print(f"ok   {spec.key:<12} {len(pdf):>7} bytes  {seconds:5.1f}s")

    if failures:
        joined = ", ".join(failures)
        print(f"\n{len(failures)} template(s) did not compile: {joined}", file=sys.stderr)
        return 1
    print(f"\nAll {len(BUILTIN_TEMPLATES)} templates compiled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
