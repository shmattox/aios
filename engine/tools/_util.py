"""_util.py — shared low-level helpers for the aios engine tools (A108).

Started as the consolidation of the ONE helper family that is byte-identical across every tool:
`utf8_stdio`. The other flagged families (`_read_json`, `_atomic_write`, `_die`, `_now`,
`_win_long`) have BEHAVIORALLY DIVERGED between call sites (long-path wrapping, extra exception
types, makedirs/retry/parse-verify, stdout-vs-stderr, differing signatures) and are deliberately
NOT collapsed here — a naive merge would change behavior, contradicting the "zero behavior change"
contract. See the A108 backlog line for the per-family divergence evidence; each needs a canonical
semantics decision before it can join this module.

Stdlib only; imported by sibling tools via the established `engine/tools`-on-sys.path pattern.
"""
import sys


def utf8_stdio():
    """Force UTF-8 on stdout/stderr — engine records carry emoji/flag glyphs (⚑, 🔵) and a native
    Windows console defaults to cp1252, which would crash the JSON print. A non-Windows console is
    already UTF-8, so this only ever helps."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
