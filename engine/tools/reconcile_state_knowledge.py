#!/usr/bin/env python3
"""reconcile_state_knowledge.py — state→wiki economic-figure drift detector (A104, Pass A).

Zero-LLM. Reads each live-KB wiki page's `snapshots:` anchor (a pipe-delimited scalar list —
"<state_key>|<field>|<value>|<as_of>|<track>", e.g.
"familyoffice/assets/some-loan|balance|2059169|2026-05-30|true"), resolves the referenced
`state/domains` row, compares deterministically, and on drift stages a corrected page + enqueues a
`review`-lane draft (the Strike-correction shape) that the existing gate ships to the wiki.

Contract (from the A104 spec/plan):
  * Reads `state/domains` (typed rows), NEVER Notion; never auto-copies an economic value as truth —
    the proposal is a *candidate* and the gate validates it vs Drive at ship (economic KBs stay
    human-gated). This tool only stages a draft + enqueues via `queue_tx`; the gate is the sole writer.
  * `state_key` resolves to `<env_root>/state/domains/<silo>/tables/<table>/<slug>.md` (silo/table
    are the first two `/`-segments; the slug is the remainder and may contain `/`).
  * Thresholds are profile knobs read from `<env_root>/profile/domains.yaml` `reconcile:`
    (`value_threshold` default 0.02, `abs_floor` default 1.0, `stale_days` default 30); unset → defaults.
  * Exit 0 always; a malformed page is counted as a parse warning, never a crash (degrade-silent).

The `snapshots:` anchor contract in the FamilyOffice KB schema is an INSTANCE concern (env-side);
this module owns only the wire format documented above.
"""
import argparse
import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_validate import _extract_frontmatter  # noqa: E402  (stdlib YAML-subset reader; returns a dict)

_DEFAULTS = {"value_threshold": 0.02, "abs_floor": 1.0, "stale_days": 30}


# ─────────────────────────── anchor parsing (Task 1) ───────────────────────────

def _frontmatter(page_path):
    """Parse a page's YAML frontmatter into a dict, or {} on any read/parse failure (degrade-silent)."""
    try:
        fm = _extract_frontmatter(Path(page_path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return fm if isinstance(fm, dict) else {}


def _anchors_raw(page_path):
    snaps = _frontmatter(page_path).get("snapshots")
    return [s for s in snaps if isinstance(s, str)] if isinstance(snaps, list) else []


def _split(raw):
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 5:
        raise ValueError("arity")
    state_key, field, value, as_of, track = parts
    num = float(value)
    if not math.isfinite(num):  # reject nan/inf — a non-finite anchor would silently suppress drift
        raise ValueError("non-finite value")
    return {"state_key": state_key, "field": field, "value": num,
            "as_of": as_of, "track": track.lower() == "true", "raw": raw}


def parse_anchors(page_path):
    """Return the well-formed `snapshots:` anchors on a page (malformed entries skipped)."""
    out = []
    for raw in _anchors_raw(page_path):
        try:
            out.append(_split(raw))
        except (ValueError, TypeError):
            continue
    return out


def parse_errors(page_path):
    """Return the raw text of each malformed `snapshots:` entry (so a caller can surface a count)."""
    errs = []
    for raw in _anchors_raw(page_path):
        try:
            _split(raw)
        except (ValueError, TypeError):
            errs.append(raw)
    return errs


# ─────────────────────────── state-row reader (Task 2) ───────────────────────────

def _coerce_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_state_field(env_root, state_key, field):
    """Resolve `<env_root>/state/domains/<silo>/tables/<table>/<slug>.md` and read `field`.

    Returns `None` when the file is absent; else `{value, last_synced, found}` where `found` is
    False for a present row whose `field` is null/missing/non-numeric."""
    parts = state_key.split("/", 2)
    if len(parts) != 3:
        return None
    # Defense-in-depth: never let a `..` segment in a (vault-authored) state_key escape state/domains.
    if any(seg == ".." for seg in re.split(r"[\\/]", state_key)):
        return None
    silo, table, slug = parts
    path = Path(env_root) / "state" / "domains" / silo / "tables" / table / f"{slug}.md"
    if not path.is_file():
        return None
    fm = _frontmatter(path)
    val = _coerce_num(fm.get(field))
    return {"value": val, "last_synced": fm.get("last_synced"), "found": val is not None}


# ─────────────────────────── drift comparison (Task 3) ───────────────────────────

def _days_between(a, b):
    try:
        ya = date.fromisoformat(str(a)[:10])
        yb = date.fromisoformat(str(b)[:10])
        return abs((yb - ya).days)
    except (ValueError, TypeError):
        return None


def evaluate(anchor, state, *, value_threshold=0.02, abs_floor=1.0, stale_days=30, today):
    """Return `None` when no action (no drift, `track:false`, state absent/unfound), else
    `{reason: "value"|"stale", target_value, as_of_new, delta}`. `today` is passed in (determinism)."""
    if not anchor.get("track") or not isinstance(state, dict) or not state.get("found"):
        return None
    sv, av = state["value"], anchor["value"]
    delta = abs(sv - av)
    base = {"target_value": sv, "as_of_new": today, "delta": delta}
    if delta > abs_floor and (av == 0 or delta / abs(av) > value_threshold):
        return {"reason": "value", **base}
    # Staleness only RE-DATES a snapshot whose value already matches state (delta <= abs_floor). A
    # nonzero-but-within-threshold delta is treated as "close enough" — neither a value nor a stale
    # proposal (don't nag over rounding noise, don't re-date). Requires an old anchor + a genuinely
    # newer state row.
    if delta > abs_floor:
        return None
    age = _days_between(anchor["as_of"], today)
    fresher = _days_between(anchor["as_of"], state.get("last_synced"))
    if age is not None and age > stale_days and fresher not in (None, 0) \
            and str(state.get("last_synced", ""))[:10] > str(anchor["as_of"])[:10]:
        return {"reason": "stale", **base}
    return None


# ─────────────────────────── dedup (Task 4) ───────────────────────────

def dedupe_key(page_rel, state_key, target_value):
    return f"{page_rel}|{state_key}|{target_value:.2f}"


def already_proposed(queue_path, key):
    """True if any queue item (ANY stage, incl. `rejected`) carries `reconcile.dedupe_key == key` —
    so a rejected refresh is never re-proposed until the state value changes again."""
    try:
        d = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    q = d.get("queue") if isinstance(d, dict) else None
    return any(isinstance(it, dict) and (it.get("reconcile") or {}).get("dedupe_key") == key
               for it in (q or []))


# ─────────────────────────── proposal emission (Task 5) ───────────────────────────

def _conflict_key(page_path, kb):
    """Derive the wiki-shape conflict_key `<kb>/wiki/<tail>` from a page under `.../wiki/...`."""
    s = str(page_path).replace("\\", "/")
    tail = s.split("/wiki/", 1)[-1] if "/wiki/" in s else Path(page_path).name
    return f"{kb}/wiki/{tail}"


def build_refresh(page_path, anchor, verdict, kb, vault_folder, now_utc="1970-01-01T00:00:00Z"):
    """Return `{staged_text, item}` — the corrected page text + the review-lane draft item.

    `staged_text` rewrites the matching `snapshots:` entry's value+as_of to the target, and the
    FIRST body occurrence of the old integer to the new (plain string op, body-scoped — no regex
    figure-hunting; that is a non-goal). If the prose figure is absent, prose is left untouched and
    the item's `rec_reason` notes it. `item` is the Strike-correction review-lane draft the gate ships."""
    page_path = Path(page_path)
    text = page_path.read_text(encoding="utf-8")
    old_int = format(int(anchor["value"]))
    new_int = format(int(verdict["target_value"]))
    new_anchor = f'{anchor["state_key"]}|{anchor["field"]}|{new_int}|{verdict["as_of_new"]}|true'
    staged = text.replace(anchor["raw"], new_anchor, 1)

    # Rewrite the first prose occurrence of the old figure — BODY ONLY (after the closing '---' fence),
    # so a value that coincidentally appears inside frontmatter is never touched.
    lines = staged.splitlines(keepends=True)
    fences = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    body_start = (fences[1] + 1) if len(fences) >= 2 else 0
    head, body = "".join(lines[:body_start]), "".join(lines[body_start:])
    prose_hit = old_int in body
    if prose_hit:
        staged = head + body.replace(old_int, new_int, 1)

    slug = page_path.stem
    conflict_key = _conflict_key(page_path, kb)
    dk = dedupe_key(conflict_key, anchor["state_key"], verdict["target_value"])
    item = {
        "id": f"{kb}-reconcile-{slug}-{verdict['as_of_new']}",
        "stage": "awaiting", "lane": "review", "kb": kb, "kb_class": "decision",
        "conflict_key": conflict_key, "source": "reconcile", "recommended": "approve",
        "rec_reason": (f"state→wiki reconcile ({verdict['reason']} drift): "
                       f"{anchor['state_key']}.{anchor['field']} moved {old_int}→{new_int}; "
                       f"snapshot re-dated {verdict['as_of_new']}. "
                       f"Validate vs current statement at ship (Paper-Governs)."
                       + ("" if prose_hit else " NOTE: prose figure not auto-found; check the body.")),
        "draft_path": f"{vault_folder}/wiki/staging/{slug}.md",
        "first_drafted_utc": now_utc,
        "reconcile": {"dedupe_key": dk, "state_key": anchor["state_key"],
                      "target_value": verdict["target_value"]},
        "history": [],
    }
    return {"staged_text": staged, "item": item}


# ─────────────────────────── CLI scan + emit (Task 6/7) ───────────────────────────

def _load_knobs(env_root):
    """Read `<env_root>/profile/domains.yaml` `reconcile:` knobs; unset → defaults (degrade-silent)."""
    knobs = dict(_DEFAULTS)
    prof = Path(env_root) / "profile" / "domains.yaml"
    try:
        from state_validate import _parse_yaml
        data = _parse_yaml(prof.read_text(encoding="utf-8"))
        rec = data.get("reconcile") if isinstance(data, dict) else None
        if isinstance(rec, dict):
            for k in _DEFAULTS:
                if rec.get(k) is not None:
                    coerced = _coerce_num(rec.get(k))
                    if coerced is not None:
                        knobs[k] = coerced if k != "stale_days" else int(coerced)
    except (OSError, ValueError):
        pass
    return knobs


def _iter_pages(vault_root, folder):
    base = Path(vault_root) / folder / "wiki"
    if not base.is_dir():
        return
    for p in sorted(base.rglob("*.md")):
        # never scan the staging husks we (or ingest) write — only canonical pages
        if "/staging/" in str(p).replace("\\", "/"):
            continue
        yield p


def _enqueue(env_root, queue_path, staged_rel, staged_text, item, vault_root):
    """Write the staged draft under the vault, then `queue_tx.py add` the item (subprocess-isolated)."""
    staged_abs = Path(vault_root) / staged_rel
    staged_abs.parent.mkdir(parents=True, exist_ok=True)
    staged_abs.write_text(staged_text, encoding="utf-8")
    tools = Path(__file__).resolve().parent
    tmp = Path(queue_path).parent / f".reconcile-add-{item['id']}.json"
    tmp.write_text(json.dumps([item]), encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, str(tools / "queue_tx.py"), "add",
                            str(queue_path), str(tmp)], capture_output=True, text=True)
        return r.returncode == 0
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def run(env_root, vault_root, kb_map, today, emit=False):
    """Scan each live KB's wiki for drifted anchors; report (or, with emit, stage+enqueue) proposals."""
    env_root = Path(env_root)
    queue_path = env_root / "state" / "queue.json"
    knobs = _load_knobs(env_root)
    proposals, warnings, emitted = 0, 0, 0
    details = []
    for kb, folder in (kb_map or {}).items():
        for page in _iter_pages(vault_root, folder):
            warnings += len(parse_errors(page))
            for anchor in parse_anchors(page):
                try:
                    state = read_state_field(env_root, anchor["state_key"], anchor["field"])
                    verdict = evaluate(anchor, state, today=today, **knobs)
                    if not verdict:
                        continue
                    conflict_key = _conflict_key(page, kb)
                    key = dedupe_key(conflict_key, anchor["state_key"], verdict["target_value"])
                    if already_proposed(queue_path, key):
                        continue
                    proposals += 1
                    details.append({"page": str(page), "state_key": anchor["state_key"],
                                    "reason": verdict["reason"], "target_value": verdict["target_value"]})
                    if emit:
                        built = build_refresh(page, anchor, verdict, kb, folder)
                        if _enqueue(env_root, queue_path, built["item"]["draft_path"],
                                    built["staged_text"], built["item"], vault_root):
                            emitted += 1
                except Exception:  # noqa: BLE001 — a single bad page never crashes the sweep
                    warnings += 1
    return {"proposals": proposals, "emitted": emitted, "parse_warnings": warnings, "details": details}


def _utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def render(result):
    """One-line health summary for the gather to lift (like standing_checks / pipeline_health)."""
    n, m = result["proposals"], result["parse_warnings"]
    if n == 0 and m == 0:
        return ""
    warn = f" · {m} parse warning(s)" if m else ""
    return f"♻ reconcile: {n} drift proposal(s) staged{warn}"


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="state→wiki economic-figure reconcile detector (A104)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="scan live KBs for drifted economic snapshots")
    r.add_argument("--env-root", required=True)
    r.add_argument("--vault-root", required=True)
    r.add_argument("--kb-map", default="{}", help="JSON {kb: vault_folder}")
    r.add_argument("--today", required=True, help="YYYY-MM-DD (determinism — no date.today())")
    r.add_argument("--emit", action="store_true", help="stage drafts + enqueue via queue_tx")
    r.add_argument("--json", action="store_true", help="print the machine-readable result")
    args = ap.parse_args(argv)
    try:
        kb_map = json.loads(args.kb_map)
    except ValueError:
        kb_map = {}
    result = run(args.env_root, args.vault_root, kb_map, args.today, emit=args.emit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        line = render(result)
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
