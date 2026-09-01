#!/usr/bin/env python3
"""Parse BACKLOG.md items and map them to board stations.  # see A109 spec

Item shape (the b2g/factory contract): open `- [ ] **ID** — headline`,
done `- [x] **ID** — ...`, seed `- ◷ **ID ...`; ids letters+digits no-hyphen.
Deterministic, zero-LLM. Read-only.
"""
import datetime as _dt
import re

_ITEM_RE = re.compile(r"^- (?P<glyph>\[ \]|\[x\]|◷) \*\*(?P<id>[A-Za-z]+\d+)(?P<rest>.*)$")
# A144: an id carrying a LETTER SUFFIX (`PS392b`, `A26a`) is refused outright, because `_ITEM_RE`
# would otherwise match only its numeric stem and silently truncate it. That truncation is not a
# cosmetic bug: a `b` item is normally split off a FINISHED parent, so the stem lands in
# `done_block_ids`, `select_drainable` drops it, and genuinely open work becomes permanently
# invisible — never drained, never surfaced for a stamp decision, and rendered with the orphaned
# suffix as its one-character headline. Found hiding three real open-place items, one of them the
# venture's standing v0 success metric.
#
# Rejecting is the deliberate choice over widening the id pattern (Seth, 2026-09-01): ids are the
# join key for standup.json, .factory/deltas/*.json and backlog_fold, so teaching the parser to
# accept suffixes would silently re-key existing state. This keeps the contract the docstring
# already states — letters+digits, nothing after.
_SUFFIXED_ID_RE = re.compile(r"^- (?:\[ \]|\[x\]|◷) \*\*(?P<id>[A-Za-z]+\d+[A-Za-z]+)\*\*")


class BacklogIdError(ValueError):
    """A backlog line whose id violates the letters+digits contract. Raised, never swallowed:
    silently mangling one is exactly the failure this exists to prevent."""
_CLOSED_RE = re.compile(r"✅ (\d{4}-\d{2}-\d{2})")
_STATES = {"[ ]": "open", "[x]": "done", "◷": "seed"}
_MARKER_CHARS = ("✋", "⛔", "⚠", "↪", "▶", "⏳")
SHIPPED_WINDOW_DAYS = 2


def parse_backlog(text):
    items = []
    for line in text.splitlines():
        probe = line.strip() if line.startswith("- ") else line
        bad = _SUFFIXED_ID_RE.match(probe)
        if bad:
            raise BacklogIdError(
                f"backlog id {bad.group('id')!r} has a letter suffix; ids are letters+digits with "
                f"nothing after (A144). Rename it to its own id — a suffixed id is silently "
                f"truncated to its stem and the item then disappears from the drain selector. "
                f"Offending line: {probe[:120]}"
            )
        m = _ITEM_RE.match(probe)
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
