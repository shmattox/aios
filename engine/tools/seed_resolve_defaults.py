#!/usr/bin/env python3
"""seed_resolve_defaults.py — idempotently seed the default `resolve:` block into a profile's
`domains.yaml`, so the task-resolution layer is not inert on a fresh install (A33).

Invoked by /aios:setup Phase 5. A checked-in tool rather than a prose "also write a resolve:
block" step ON PURPOSE: a prose step the model skips is exactly what left the resolution layer
dormant (A31 finding) — this is deterministic, idempotent, and Refresh-safe. Fact-free: the block
it seeds is a starter default the person edits; the engine reads keywords/models FROM the profile,
never hardcodes them. Stdlib-only (no yaml dependency — line-based check + verbatim append).
"""
import argparse, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))                       # engine/tools
DEFAULT_TEMPLATE = os.path.join(os.path.dirname(_HERE), "templates", "resolve-defaults.yaml")  # engine/templates


def has_resolve_block(text):
    """True if the domains.yaml text already declares a top-level `resolve:` key.
    NB: to *disable* resolution, empty the block's keyword list rather than deleting the block —
    a present-but-empty block reads True here and survives a Refresh; a deleted one gets re-seeded."""
    return any(line.startswith("resolve:") for line in text.splitlines())


def template_keywords(template_path=DEFAULT_TEMPLATE):
    """Extract the default economic_keywords from the template — used by the test suite to prove the
    SEEDED defaults actually flag a task. The engine itself reads keywords from the parsed profile
    (domains.yaml), NOT from this template. Minimal inline-`[...]`-list parse, tolerant of wrap;
    returns [] if the marker or brackets are absent."""
    with open(template_path, encoding="utf-8") as f:
        # drop comment lines first: a prose mention like `economic_keywords: []` must not shadow the
        # real key (continuation lines of a wrapped inline list are not comments, so they survive).
        text = "\n".join(ln for ln in f.read().splitlines() if not ln.lstrip().startswith("#"))
    marker = "economic_keywords:"
    i = text.find(marker)
    if i == -1:
        return []
    rest = text[i + len(marker):]
    lb, rb = rest.find("["), rest.find("]")
    if lb == -1 or rb == -1:
        return []
    inner = rest[lb + 1:rb]
    return [w.strip() for w in inner.replace("\n", " ").split(",") if w.strip()]


def seed(domains_path, template_path=DEFAULT_TEMPLATE):
    """Append the default resolve: block to domains_path if it has none.

    Returns True if the block was appended, False if one already existed (idempotent — a Refresh
    over an existing install preserves the person's tuned block). Ensures a newline boundary so the
    appended block can never merge onto the file's last line.
    """
    with open(template_path, encoding="utf-8") as f:
        block = f.read()
    existing = ""
    if os.path.exists(domains_path):
        with open(domains_path, encoding="utf-8") as f:
            existing = f.read()
    if has_resolve_block(existing):
        return False
    # Guarantee a newline boundary so the appended block can never merge onto the file's last line.
    # (The template also opens with a blank line; an extra separating newline here is harmless.)
    sep = "" if (not existing or existing.endswith("\n")) else "\n"
    with open(domains_path, "a", encoding="utf-8") as f:
        f.write(sep + block)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Seed the default resolve: block into a domains.yaml (idempotent)")
    ap.add_argument("domains_yaml", help="path to <env_root>/profile/domains.yaml")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE, help="override the default block template")
    args = ap.parse_args(argv)
    if seed(args.domains_yaml, args.template):
        print("SEED: appended default resolve: block -> " + args.domains_yaml)
    else:
        print("SKIP: resolve: block already present, left as-is -> " + args.domains_yaml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
