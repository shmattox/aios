#!/usr/bin/env python3
"""setup_render.py — deterministic renderer for the generated orchestrator CLAUDE.md (A25).

The setup skill's judgment is COMPOSING the token values (which ritual shape an install gets);
the substitution, the Session-End renumber rule, and the unresolved-token guard are string
mechanics — they live here (setup Contracts 3 and 6 as code, not prose).

  substitute : every {{TOKEN}} in the template is replaced from the tokens JSON.
  renumber   : all numbered list items inside the "## 2. Session-End Ritual" section are
               renumbered sequentially after substitution, so a generated block of ANY length
               reads continuously into the template's hardcoded trailing items.
  guard      : any remaining '{{' fails loud (exit 1, offending tokens listed) — a file with a
               literal {{...}} never ships.

Usage:
  python setup_render.py --template <CLAUDE.template.md> --tokens <tokens.json> --out <CLAUDE.md>
  python setup_render.py --template … --tokens … --print          # stdout, no file write
"""
import argparse
import json
import re
import sys


def render(template_text, tokens):
    out = template_text
    for k, v in tokens.items():
        out = out.replace("{{" + k + "}}", str(v))
    out = _renumber_section(out, "## 2. Session-End Ritual")
    leftover = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", out)))
    return out, leftover


def _renumber_section(text, heading):
    """Renumber `N.` list items sequentially within one `## ` section (Contract 3)."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip().startswith(heading)), None)
    if start is None:
        return text
    n = 0
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            break
        m = re.match(r"^(\s*)\d+\.(\s)", lines[i])
        if m:
            n += 1
            lines[i] = re.sub(r"^(\s*)\d+\.", rf"\g<1>{n}.", lines[i], count=1)
    return "".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="setup_render.py",
                                 description="Render the generated orchestrator CLAUDE.md.")
    ap.add_argument("--template", required=True)
    ap.add_argument("--tokens", required=True, help="JSON file: {TOKEN: value, ...}")
    out_arg = ap.add_mutually_exclusive_group(required=True)
    out_arg.add_argument("--out")
    out_arg.add_argument("--print", action="store_true", dest="to_stdout")
    args = ap.parse_args(argv)

    with open(args.template, encoding="utf-8") as f:
        template = f.read()
    with open(args.tokens, encoding="utf-8") as f:
        tokens = json.load(f)
    if not isinstance(tokens, dict):
        print("FAIL: tokens file must be a JSON object", file=sys.stderr)
        return 1

    rendered, leftover = render(template, tokens)
    if leftover:
        print("FAIL: unresolved tokens (never ship a literal {{...}}): " + ", ".join(leftover),
              file=sys.stderr)
        return 1
    if args.to_stdout:
        sys.stdout.write(rendered)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"OK: rendered {args.out} ({len(rendered.splitlines())} lines, 0 unresolved tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
