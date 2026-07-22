#!/usr/bin/env python3
"""Parse BACKLOG.md items and map them to board stations.  # see A109 spec

Item shape (the b2g/factory contract): open `- [ ] **ID** — headline`,
done `- [x] **ID** — ...`, seed `- ◷ **ID ...`; ids letters+digits no-hyphen.
Deterministic, zero-LLM. Read-only.
"""
import datetime as _dt
import re

_ITEM_RE = re.compile(r"^- (?P<glyph>\[ \]|\[x\]|◷) \*\*(?P<id>[A-Za-z]+\d+)(?P<rest>.*)$")
_CLOSED_RE = re.compile(r"✅ (\d{4}-\d{2}-\d{2})")
_STATES = {"[ ]": "open", "[x]": "done", "◷": "seed"}
_MARKER_CHARS = ("✋", "⛔", "⚠", "↪", "▶", "⏳")
SHIPPED_WINDOW_DAYS = 2


def parse_backlog(text):
    items = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line.strip() if line.startswith("- ") else line)
        if not m:
            continue
        rest = m.group("rest")
        closed = _CLOSED_RE.search(rest)
        items.append({
            "id": m.group("id"),
            "state": _STATES[m.group("glyph")],
            "headline": rest.lstrip("*— ").split("**", 1)[0].strip()[:200],
            "gate_human": "[GATE: human]" in rest,
            "markers": [c for c in _MARKER_CHARS if c in rest],
            "closed_date": closed.group(1) if closed else None,
        })
    return items


def station_for(item, standup_ids, today=None):
    """Deterministic station mapping (spec §Verb matrix / plan deviation 3)."""
    today = today or _dt.date.today().isoformat()
    group = standup_ids.get(item["id"])
    if item["state"] == "done":
        if not item["closed_date"]:
            return None
        age = (_dt.date.fromisoformat(today)
               - _dt.date.fromisoformat(item["closed_date"])).days
        return "shipped" if 0 <= age <= SHIPPED_WINDOW_DAYS else None
    if item["state"] == "seed":
        return "incoming"
    # open items
    if item["gate_human"] or "✋" in item["markers"] or "⛔" in item["markers"] \
            or group in ("needs-you", "stuck"):
        return "needs_you"
    if "▶" in item["markers"] or "↪" in item["markers"] or "⏳" in item["markers"] \
            or group == "handed-off":
        return "in_motion"
    return "incoming"
