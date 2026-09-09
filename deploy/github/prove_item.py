#!/usr/bin/env python3
"""prove_item.py — run a backlog item's declared `verify:` commands in CI, off the author's machine.

The item's acceptance chain is read from BACKLOG.md at the PR's MERGE-BASE, so a PR cannot soften
the commands it is graded against (the same rule as the env repo's `Scripts/prove/checks.py`, whose
grammar this mirrors line for line — keep them in step). An item with no ``verify: `<cmd>` `` line
is reported `unproven`, never silently passed: that count is the visible hole, not a green.

    python3 scripts/prove_item.py <ITEM-ID> [--base <sha>] [--strict]

Exit 0: every declared command exited 0 (or none were declared / the id is not in this ledger, and not --strict).
Exit 1: a declared command failed, or --strict and the item declares none.
Exit 2: --strict and the item is not in this repo's ledger at all.
"""
import json, os, re, subprocess, sys, time

for _stream in (sys.stdout, sys.stderr):        # a test runner's glyphs must not crash the report on a cp1252 console
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_ITEM = r"^- (?:\[[ xX]\]|◷) \*\*%s\*\*"
_BLOCK_END = re.compile(r"^(?:- (?:\[[ xX]\]|◷) \*\*[A-Za-z]+[0-9]+\*\*|#)")
_VERIFY = re.compile(r"verify:\s*`([^`]+)`")
_ANCHOR = re.compile(r"anchor:\s*`([^`]+)`")


def item_block(text, item_id):
    """Headline + indented continuation, bounded by the next ITEM marker or heading — never a blank line."""
    lines = text.splitlines()
    head = re.compile(_ITEM % re.escape(item_id))
    for i, line in enumerate(lines):
        if head.match(line):
            block = [line]
            for nxt in lines[i + 1:]:
                if _BLOCK_END.match(nxt):
                    break
                block.append(nxt)
            return "\n".join(block).rstrip() + "\n"
    return ""


def acceptance(text, item_id):
    return [ln for ln in item_block(text, item_id).splitlines() if ln.startswith("  ") and "acceptance:" in ln]


def claims(text, item_id):
    """(verify_cmds, unproven_lines) from the item's acceptance chain."""
    acc = acceptance(text, item_id)
    cmds = [c.strip() for ln in acc for c in _VERIFY.findall(ln)]
    unproven = [ln.strip() for ln in acc if not _VERIFY.search(ln) and not _ANCHOR.search(ln)]
    return cmds, unproven


def ledger_at(base):
    """BACKLOG.md at the merge-base (frozen), else the working copy for a brand-new item (unfrozen)."""
    if base:
        r = subprocess.run(["git", "show", "%s:BACKLOG.md" % base], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return r.stdout, True
    with open("BACKLOG.md", encoding="utf-8") as f:
        return f.read(), False


def run(cmd, timeout=900):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        code, tail = p.returncode, (p.stdout + p.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        code, tail = 124, "timeout after %ss" % timeout
    return code, tail, round(time.time() - t0, 1)


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__); return 2
    item, strict, base = argv[0].upper(), "--strict" in argv, None
    if "--base" in argv:
        base = argv[argv.index("--base") + 1]
    text, frozen = ledger_at(base)
    if not item_block(text, item) and frozen:          # new on this branch — grade the working copy
        text, frozen = ledger_at(None)
    if not item_block(text, item):
        # An env-ledger id on a branch here (H157 itself) is normal in this fleet; only --strict fails it.
        print("::%s::%s is not in this repo's BACKLOG.md — nothing to prove here"
              % ("error" if strict else "warning", item))
        return 2 if strict else 0
    cmds, unproven = claims(text, item)
    rows, failed, manual = [], 0, []
    for cmd in cmds:
        # Playwright specs are the manual-oracle lane: they need browsers and a live world
        # (LADDER_WORLD_URL), which this job does not provision. Report them, never run them here.
        if cmd.startswith("npx playwright") and os.environ.get("PROVE_RUN_E2E") != "1":
            manual.append(cmd); rows.append((cmd, "manual", 0)); continue
        code, tail, secs = run(cmd)
        failed += code != 0
        rows.append((cmd, code, secs))
        print("%s exit=%s %ss :: %s" % ("PASS" if code == 0 else "FAIL", code, secs, cmd))
        if code != 0:
            print(tail)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = ["### prove — %s (%s acceptance)" % (item, "frozen at merge-base" if frozen else "UNFROZEN: item is new on this branch"),
             "", "| claim | exit | s |", "|---|---|---|"]
    lines += ["| `%s` | %s | %s |" % (c.replace("|", "\\|"), code, s) for c, code, s in rows]
    if not cmds:
        lines.append("| _no `verify:` line declared_ | unproven | — |")
    if manual:
        lines.append("")
        lines.append("**manual lane: %d Playwright claim(s) not run here** — they need browsers and a live world; run them in the e2e job or by hand and cite the run." % len(manual))
    if unproven:
        lines.append("")
        lines.append("**unproven: %d of %d acceptance line(s)** carry no runnable claim — declare ``acceptance: verify: `<cmd>` `` to gate them." % (len(unproven), len(unproven) + len(cmds)))
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print(json.dumps({"item": item, "frozen": frozen, "verify": len(cmds), "failed": failed,
                      "manual": len(manual), "unproven": len(unproven)}))
    if not cmds:
        print("::%s::%s declares no verify: line — unproven (%d acceptance line(s))"
              % ("error" if strict else "warning", item, len(unproven)))
        return 1 if strict else 0
    return 1 if failed else 0


def _self_test():
    text = ("- [ ] **PS1** — thing\n  - acceptance: verify: `true`; prose too\n  - acceptance: only prose\n"
            "  - acceptance: anchor: `false`\n\n- [ ] **PS2** — other\n  - acceptance: verify: `false`\n## Done\n"
            "- [x] **PS3** — closed\n  - acceptance: verify: `echo hi`\n")
    assert claims(text, "PS1") == (["true"], ["- acceptance: only prose"])
    assert claims(text, "PS2") == (["false"], [])
    assert item_block(text, "PS3").count("\n") == 2 and claims(text, "PS4") == ([], [])
    assert item_block(text, "PS1").count("PS2") == 0          # bounded by the next item, not a blank line
    print("self-test ok")


if __name__ == "__main__":
    sys.exit(_self_test() if sys.argv[1:] == ["--self-test"] else main(sys.argv[1:]))
