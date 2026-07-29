#!/usr/bin/env python3
"""brief_refs — deterministic structured drill-down refs on brief items (A109 "refs at source").

The dashboard's card renders a "Drill down" box of source-tagged links (STATE / DRIVE / KB /
NOTION). Gate (held) items already carry the canonical-roles fields (state_path / papered_source /
draft_path); act items (needs-you tasks) reference their context inside a free-text citation string.
This tool folds BOTH into ONE structured `refs` list on every item at gather time, so the UI renders
a single uniform drill-down instead of parsing citation strings client-side — the "populate the same
way" fix (model authors the prose + cite; the engine emits the structured refs; the UI renders).

Ref shape (exactly what the dashboard's Ref component consumes): {tag, label, route?|open?|mock?}
  - route: an in-app hash route (state records -> the Mirror browser)
  - open : a real URL to open (http(s) drive/web links)
  - mock : an informative message for a target the dashboard can't deep-link yet (Notion, Obsidian)

Zero-LLM, idempotent (rebuilt from source fields each run — never reads its own output), read-only
except the `refs` key it writes. Fact-free: every path/target comes from the item.
"""
import json
import os
import re
import sys
import urllib.parse

_STATE_RE = re.compile(r"state/[^\s;,)]+")
# capture the collection id + the role word the cite names right after it (task/decision/page/…),
# so the drill-down shows a readable "Notion · task" label instead of a raw collection:// uuid.
_COLLECTION_RE = re.compile(r"collection://([A-Za-z0-9-]{8,})(?:\s+(task|decision|page|row|record|note))?", re.I)
_URL_RE = re.compile(r"https?://[^\s;,)]+")


def _short(p):
    p = str(p or "").replace("\\", "/")
    parts = [x for x in p.split("/") if x]
    return "…/" + "/".join(parts[-3:]) if len(parts) > 4 else p


def _obsidian_url(path, vault_name):
    """A real `obsidian://open` deep-link for a vault-relative KB path, or None without a vault name.
    Deterministic — the vault name is config passed in by the gather, not read from SSOT here."""
    if not vault_name or not path:
        return None
    rel = str(path).replace("\\", "/").lstrip("/")
    return (f"obsidian://open?vault={urllib.parse.quote(vault_name)}"
            f"&file={urllib.parse.quote(rel)}")


def build_refs(item, vault_name=None):
    """Deterministic structured drill-down refs for one brief item (act or held). Canonical-roles
    fields first (held), then the action thread + parsed citations (act); deduped by (tag,label).
    `vault_name` (config, passed in) turns KB refs into real `obsidian://` deep-links."""
    out, seen = [], set()

    def add(ref):
        key = (ref["tag"], ref["label"])
        if ref["label"] and key not in seen:
            seen.add(key)
            out.append(ref)

    # held / gate items — the canonical-roles fields (state -> Mirror; drive/kb -> informative open)
    if item.get("state_path"):
        add({"tag": "state", "label": _short(item["state_path"]), "route": "#/mirror",
             "mock": f"opens {item['state_path']} in the Mirror"})
    paper = item.get("papered_source") or ""
    if _URL_RE.match(paper):
        add({"tag": "drive", "label": paper, "open": paper})
    elif paper:
        add({"tag": "drive", "label": _short(paper), "mock": f"opens “{paper}” in Google Drive"})
    if item.get("draft_path"):
        dp = item["draft_path"]
        kb = {"tag": "kb", "label": _short(dp)}
        obs = _obsidian_url(dp, vault_name)
        if obs:
            kb["open"] = obs                      # real obsidian:// deep-link when the vault is known
        else:
            kb["mock"] = f"opens {_short(dp)} in Obsidian"
        add(kb)

    # act items — the action thread, then any references inside the system-voice citation
    tid = item.get("thread_id") or (item.get("in_motion") or {}).get("thread_id")
    thread_file = f"state/threads/{tid}.md" if tid else None
    if tid:
        add({"tag": "state", "label": f"thread · {tid}", "route": "#/mirror",
             "mock": f"opens the {tid} action thread"})
    sv = item.get("system_voice")
    cite = (sv.get("cite") if isinstance(sv, dict) else "") or ""
    for m in _STATE_RE.finditer(cite):
        if m.group(0) == thread_file:
            continue  # same file as the "thread · {tid}" row above — one row per resource
        add({"tag": "state", "label": m.group(0), "route": "#/mirror",
             "mock": f"opens {m.group(0)} in the Mirror"})
    for m in _COLLECTION_RE.finditer(cite):
        target = f"collection://{m.group(1)}"
        role = (m.group(2) or "").lower()
        add({"tag": "notion", "label": f"Notion · {role}" if role else "Notion record",
             "mock": f"opens {target} in Notion"})
    for m in _URL_RE.finditer(cite):
        add({"tag": "drive", "label": m.group(0), "open": m.group(0)})
    return out


def _iter_items(cache):
    for it in cache.get("act") or []:
        yield it
    for it in cache.get("held") or []:
        yield it
    stations = cache.get("stations") or {}
    for node in stations.values():
        items = node if isinstance(node, list) else (node or {}).get("items", [])
        for it in items:
            yield it


def annotate_cache(cache, vault_name=None):
    """Write `refs` onto every act/held/station item that has any. Mutates in place; returns count."""
    n = 0
    for it in _iter_items(cache):
        refs = build_refs(it, vault_name=vault_name)
        if refs:
            it["refs"] = refs
            n += 1
    return n


def _atomic_write(path, cache):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    with open(tmp, encoding="utf-8") as f:
        json.load(f)
    os.replace(tmp, path)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3 or argv[1] != "annotate":
        print("usage: brief_refs.py annotate <cache.json> [--vault-name <name>]", file=sys.stderr)
        return 2
    cache_path = argv[2]
    vault_name = None
    if "--vault-name" in argv:
        i = argv.index("--vault-name")
        vault_name = argv[i + 1] if i + 1 < len(argv) else None
    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)
    n = annotate_cache(cache, vault_name=vault_name)
    _atomic_write(cache_path, cache)
    print(json.dumps({"annotated": n}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
