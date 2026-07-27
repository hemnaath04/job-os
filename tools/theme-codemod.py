#!/usr/bin/env python3
"""Codemod: map inline dark-theme color hardcodes to the light warm design tokens.

Rules (variant prefixes like hover:/focus:/group-hover: are preserved):
  text-white            -> text token by opacity  (>=.85/none=text, .6-.84=muted, <.6=dim)
  text-black            -> on-accent (used on jasmine buttons)
  border-white/N        -> border (<=.12) else border-strong
  bg-white/N            -> surface-2 (<=.045) else surface-hover
  ring-white/N          -> ring border-strong
  divide-white/N        -> divide border
Deliberately NOT touched: bg-black/* (modal scrims stay dark), gradient *-white/* stops.
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("apps/web/src")

VARIANT = r"(?P<v>(?:[a-z-]+:)*)"  # hover: focus: group-hover: dark: etc.


def opacity(tok: str) -> float:
    if tok is None or tok == "":
        return 1.0
    tok = tok.lstrip("/")
    if tok.startswith("[") and tok.endswith("]"):
        tok = tok[1:-1]
    try:
        v = float(tok)
    except ValueError:
        return 1.0
    return v / 100.0 if v > 1 else v


def text_token(op: float) -> str:
    if op >= 0.85:
        return "--color-text"
    if op >= 0.6:
        return "--color-text-muted"
    return "--color-text-dim"


def repl_text_white(m):
    v = m.group("v")
    op = opacity(m.group("op"))
    return f"{v}text-[color:var({text_token(op)})]"


def repl_border_white(m):
    v = m.group("v")
    op = opacity(m.group("op"))
    tok = "--color-border" if op <= 0.12 else "--color-border-strong"
    return f"{v}border-[color:var({tok})]"


def repl_bg_white(m):
    v = m.group("v")
    op = opacity(m.group("op"))
    tok = "--color-surface-2" if op <= 0.045 else "--color-surface-hover"
    return f"{v}bg-[color:var({tok})]"


def repl_ring_white(m):
    return f'{m.group("v")}ring-[color:var(--color-border-strong)]'


def repl_divide_white(m):
    return f'{m.group("v")}divide-[color:var(--color-border)]'


OPTS = r"(?P<op>/\[[0-9.]+\]|/[0-9]+)?"
RULES = [
    (re.compile(VARIANT + r"text-white" + OPTS + r"(?![\w-])"), repl_text_white),
    (re.compile(VARIANT + r"text-black(?![\w-])"),
     lambda m: f'{m.group("v")}text-[color:var(--color-on-accent)]'),
    (re.compile(VARIANT + r"border-white" + OPTS + r"(?![\w-])"), repl_border_white),
    (re.compile(VARIANT + r"bg-white" + OPTS + r"(?![\w-])"), repl_bg_white),
    (re.compile(VARIANT + r"ring-white" + OPTS + r"(?![\w-])"), repl_ring_white),
    (re.compile(VARIANT + r"divide-white" + OPTS + r"(?![\w-])"), repl_divide_white),
]

total = 0
for f in ROOT.rglob("*.tsx"):
    src = f.read_text()
    out = src
    for rx, fn in RULES:
        out = rx.sub(fn, out)
    if out != src:
        n = sum(1 for _ in re.finditer(r"var\(--color-", out)) - sum(1 for _ in re.finditer(r"var\(--color-", src))
        f.write_text(out)
        print(f"  {f.relative_to(ROOT)}  (+{n} token refs)")
        total += 1
print(f"\nRewrote {total} files.")
