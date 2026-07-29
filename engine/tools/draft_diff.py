#!/usr/bin/env python3
"""draft_diff — a line-level diff between the current canonical page and a staged draft, for the
dashboard's "Proposed change" section (the approved mockup's red/green block). Deterministic, stdlib.

Returns rows of {op: "ctx"|"del"|"add", text}. A brand-new page (no current) is all-add. Long
unchanged runs collapse to `context` lines each end with an `@@ … @@` marker, so the operator sees
what CHANGES, not the whole file.
"""
import difflib


def compute_diff(current_text, draft_text, context=3):
    cur = (current_text or "").splitlines()
    new = (draft_text or "").splitlines()
    sm = difflib.SequenceMatcher(None, cur, new, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            block = cur[i1:i2]
            if len(block) > 2 * context + 1:
                for ln in block[:context]:
                    rows.append({"op": "ctx", "text": ln})
                rows.append({"op": "ctx", "text": f"@@ … {len(block) - 2 * context} unchanged lines … @@"})
                for ln in block[-context:]:
                    rows.append({"op": "ctx", "text": ln})
            else:
                for ln in block:
                    rows.append({"op": "ctx", "text": ln})
        else:  # delete / insert / replace
            for ln in cur[i1:i2]:
                rows.append({"op": "del", "text": ln})
            for ln in new[j1:j2]:
                rows.append({"op": "add", "text": ln})
    return rows


def summarize(rows):
    return {"added": sum(1 for r in rows if r["op"] == "add"),
            "removed": sum(1 for r in rows if r["op"] == "del")}
