#!/usr/bin/env python3
"""resolve_sweep_task.py — overnight resolve pre-sweep (A34, thin deterministic slice).

Runs as a native `type: script` scheduled task (no model in the loop). It:
  1. reads the resolve config from the profile (keywords, cache_dir, optional entities_dir),
  2. gathers open tasks from the profile's Notion task_views (headless, via notion_gather) — or a
     `--tasks-file` override for hermetic runs/tests — degrading to a CLEAN NO-OP when there is no
     token / no configured source,
  3. flags the economic tasks (resolve_sweep) and, best-effort, attaches each flagged task's
     crosswalk candidate refs from a matching entity page (resolve_fetch),
  4. writes the flag+candidate cache to `<cache_dir>/sweep.json` with a content hash so an unchanged
     task set stays WARM (no rewrite), and emits one `resolve-sweep` context-log line.

Deliberately does NOT fetch raw Drive bytes — that (model/MCP) step stays at brief time, which now
reads a warm, pre-flagged cache instead of discovering flags blind (the A31 finding). Fact-free
(every fact comes from the profile at the passed env_root); stdlib-only.
"""
import argparse, hashlib, json, os, re, subprocess, sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import resolve_sweep
import resolve_fetch
import context_log
from capture_router_task import _parse_yaml_subset            # sanctioned stdlib profile parser

STAGE = "resolve-sweep"


def _read_resolve_list(path, key):
    """A keyed list from UNDER the top-level `resolve:` block of domains.yaml, supporting BOTH inline
    `[a, b]` (incl. wrapped) and YAML block-list (`- a`) forms — block items at the key's indent
    (yaml.dump default) OR deeper — so reformatting the profile can't silently self-disable the sweep.
    Anchored to `resolve:`; inline `#` comments stripped; [] if absent. Serves economic_keywords AND
    task_source_dbs. Stdlib-only. (One of a few small list parsers in the engine — read_links_block is
    another; consolidating them is tracked debt, not done here to keep this change small.)"""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    in_resolve, i = False, 0
    while i < len(lines):
        raw, stripped = lines[i], lines[i].strip()
        indent = len(raw) - len(raw.lstrip())
        if indent == 0 and stripped and not stripped.startswith("#"):
            in_resolve = (stripped.rstrip().endswith(":") and stripped[:-1].strip() == "resolve")
        if (not in_resolve or stripped.startswith("#") or ":" not in stripped
                or stripped.partition(":")[0].strip() != key):
            i += 1
            continue
        rest = stripped.partition(":")[2].split("#", 1)[0].strip()
        if rest.startswith("["):
            buf = rest
            while "]" not in buf and i + 1 < len(lines):
                i += 1
                buf += " " + lines[i].split("#", 1)[0].strip()
            inner = buf[buf.find("[") + 1: buf.find("]") if "]" in buf else len(buf)]
            return [w.strip().strip("\"'") for w in inner.split(",") if w.strip()]
        # block list: `- item` lines at the key's indent (yaml.dump default) or deeper; a non-list
        # line or a shallower line ends the sequence.
        key_indent, out, j = indent, [], i + 1
        while j < len(lines):
            ln, s = lines[j], lines[j].strip()
            if not s or s.startswith("#"):
                j += 1
                continue
            ind = len(ln) - len(ln.lstrip())
            if not s.startswith("- ") or ind < key_indent:
                break
            item = s[2:].split("#", 1)[0].strip().strip("\"'")
            if item:
                out.append(item)
            j += 1
        return out
    return []


def _read_economic_keywords(path):
    return _read_resolve_list(path, "economic_keywords")


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return _parse_yaml_subset(f.read())


def resolve_config(env_root):
    """The resolve config from the profile, or None if resolution isn't configured.
    {keywords[], cache_dir(abs), entities_dir(abs|None), task_dbs[]}."""
    domains = os.path.join(env_root, "profile", "domains.yaml")
    if not os.path.exists(domains):
        return None
    tree = _load_yaml(domains)
    if not isinstance(tree.get("resolve"), dict):
        return None
    r = tree["resolve"]
    cache_rel = r.get("cache_dir") or "state/resolve-cache"
    cache_dir = cache_rel if os.path.isabs(cache_rel) else os.path.join(env_root, cache_rel)
    # A38: entities_dir may be a LIST (entities/ AND companies/, multiple KBs) or a scalar string
    # (back-compat). The list form parses via _read_resolve_list (inline `[a,b]` or block); a scalar
    # falls back to the raw value. Both resolve env_root-relative. `entities_dir` (singular) is kept
    # as the first dir so older callers keep working; `entities_dirs` is the full list the sweep reads.
    ent_list = _read_resolve_list(domains, "entities_dir")
    if not ent_list:
        ent_scalar = r.get("entities_dir")
        ent_list = [ent_scalar] if isinstance(ent_scalar, str) and ent_scalar else []
    entities_dirs = [p if os.path.isabs(p) else os.path.join(env_root, p) for p in ent_list]
    entities_dir = entities_dirs[0] if entities_dirs else None
    # Task source = data-source ids the headless REST gather (notion_gather) can query. NOT the brief's
    # `notion.task_views` (those are `view://` ids the REST API 404s on — only its MCP path queries views).
    task_dbs = _read_resolve_list(domains, "task_source_dbs")
    return {"keywords": _read_economic_keywords(domains), "cache_dir": cache_dir,
            "entities_dir": entities_dir, "entities_dirs": entities_dirs, "task_dbs": task_dbs,
            "domain_map": domain_group_map(env_root)}   # A35: db id -> domain-group key


def _normalize_tasks(items, domain=None):
    """Notion/normalized items -> the {id,title,body,domain} shape resolve_sweep scans. props are
    folded into body so an economic keyword in a Notes/description property still flags. `domain` is
    the source's domain-group key (A35 all-domain attribution); an item's OWN domain wins if present."""
    out = []
    for it in items or []:
        props = it.get("props") if isinstance(it, dict) else None
        body = it.get("body") or (" ".join(str(v) for v in props.values()) if isinstance(props, dict) else "")
        out.append({"id": it.get("id"), "title": it.get("title") or "", "body": body,
                    "domain": it.get("domain") or domain})
    return out


def domain_group_map(env_root):
    """{tasks_db_id: domain_group_key} from the profile's `domain_groups` block — lets the sweep tag
    each flagged figure with the domain group its source db belongs to (the A35 all-domain header).
    {} when the profile/block is absent. Fact-free: the ids come from the profile, never hardcoded."""
    domains = os.path.join(env_root, "profile", "domains.yaml")
    if not os.path.exists(domains):
        return {}
    groups = _load_yaml(domains).get("domain_groups")
    if not isinstance(groups, dict):
        return {}
    out = {}
    for key, g in groups.items():
        if isinstance(g, dict) and g.get("tasks_db"):
            out[g["tasks_db"]] = key
    return out


def gather_tasks(cfg, tasks_file=None):
    """(tasks, source). source ∈ {tasks-file, notion, none, error}. 'none' = legitimately no source
    configured (safe to cache empty); 'error' = a gather that SHOULD have worked failed (token blip,
    timeout, bad JSON) — the caller must PRESERVE the prior warm cache rather than clobber it empty."""
    if tasks_file:
        with open(tasks_file, encoding="utf-8") as f:
            raw = json.load(f)
        tasks = raw.get("tasks") if isinstance(raw, dict) else raw
        return _normalize_tasks(tasks), "tasks-file"
    if not cfg["task_dbs"]:
        return [], "none"
    argv = [sys.executable, os.path.join(_HERE, "notion_gather.py"), "tasks"]
    for db in cfg["task_dbs"]:
        argv += ["--db", db]
    argv += ["--status-exclude", "Done"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except Exception:
        return [], "error"         # timeout / spawn failure -> degraded, preserve prior cache
    if proc.returncode != 0 or not proc.stdout.strip():
        return [], "error"         # no source went live (token blip / all sources failed)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], "error"
    return _tasks_from_gather_doc(doc, cfg.get("domain_map"))


def _tasks_from_gather_doc(doc, db_domain=None):
    """(tasks, source) from a notion_gather doc. Any PARTIAL outage (a source with ok:false / error,
    or doc live:false) is treated as 'error' so the caller preserves the prior warm cache — caching a
    subset that silently drops a down source's economic tasks is the A31 black-box failure. Each
    source's items are tagged with its domain-group key via `db_domain` (A35 all-domain attribution)."""
    sources = doc.get("sources", [])
    if not sources or not doc.get("live", True) or any((not s.get("ok")) or s.get("error") for s in sources):
        return [], "error"
    items = []
    for s in sources:
        dom = (db_domain or {}).get(s.get("db"))
        items.extend(_normalize_tasks(s.get("items", []), dom))
    return items, "notion"


def _entities(entities_dirs):
    """[(basename, text)] for entity .md pages across ALL configured dirs, or [] when none is
    configured/present. A38: accepts a LIST of dirs so the crosswalk spans `entities/` AND
    `companies/` (and multiple KBs) instead of one folder — a single flagged task can then match an
    entity wherever it lives. A single-dir string (or None) is normalized here for back-compat."""
    if isinstance(entities_dirs, str):
        entities_dirs = [entities_dirs]
    out = []
    for d in (entities_dirs or []):
        if not d or not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".md"):
                try:
                    with open(os.path.join(d, name), encoding="utf-8") as f:
                        out.append((name, f.read()))
                except OSError:
                    continue
    return out


def _name_in_text(name, text):
    """Word-boundary match (min length 3) so a short slug/alias can't false-match inside an unrelated
    word (e.g. 'bay' in 'bayview', 'llc' in a longer token) and attach the wrong crosswalk refs."""
    return len(name) >= 3 and re.search(r"\b" + re.escape(name) + r"\b", text) is not None


def _candidates_for_task(task, entities):
    """Best-effort crosswalk attach: any entity whose slug or an alias appears (as a WHOLE WORD) in
    the task text contributes its candidate refs. Deterministic; [] when nothing matches."""
    text = ("%s %s" % (task.get("title", ""), task.get("body", ""))).lower()
    cands = []
    for name, etext in entities:
        info = resolve_fetch.candidates_for(etext)
        slug = os.path.splitext(name)[0].replace("-", " ").lower()
        names = [slug] + [a.lower() for a in info.get("aliases", [])]
        if any(_name_in_text(n, text) for n in names):
            for c in info.get("candidates", []):
                cands.append(dict(c, entity=name))
    return cands


def _hash(flagged):
    payload = json.dumps(sorted(flagged, key=lambda e: str(e.get("id"))), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidates_fingerprint(flagged):
    """A60: a hash of just each flagged task's id + its candidate refs (sorted) — the resolve
    worklist's ACTIONABLE shape. Unlike `content_hash` (which the brief saw churn daily from title
    edits), this is stable across title/reason churn but flips on any real change: a new or resolved
    task, or a changed candidate set. The brief demotes a run of identical fingerprints from a daily
    'INCOMPLETE' alarm to a quiet steady-state line; a flip re-alarms at full volume."""
    def refs(t):
        return sorted(str(c.get("ref")) for c in (t.get("candidates") or []))
    shape = sorted([str(t.get("id")), refs(t)] for t in flagged)
    return hashlib.sha256(json.dumps(shape, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_cache(tasks, keywords, entities, source):
    """Pure: tasks -> the sweep cache dict (sans timestamp, which the caller stamps). Each flagged
    figure carries its `domain` group; `no_paper_count`/`no_paper_domains` (A35) count the flagged
    figures the crosswalk found NO candidate governing doc for — the multi-domain 'no paper' headline
    the brief renders. Both are derived from `flagged`, so they cannot disagree with it."""
    by_id = {t.get("id"): t for t in tasks}
    flagged = []
    for f in resolve_sweep.sweep(tasks, keywords, set()):
        flagged.append({"id": f["id"], "title": f["title"], "reason": f["reason"],
                        "domain": f.get("domain"),
                        "candidates": _candidates_for_task(by_id.get(f["id"], {}), entities)})
    no_paper = [f for f in flagged if not f["candidates"]]
    return {"stage": STAGE, "source": source, "task_count": len(tasks),
            "flagged_count": len(flagged), "no_paper_count": len(no_paper),
            "no_paper_domains": sorted({f["domain"] for f in no_paper if f["domain"]}),
            "content_hash": _hash(flagged), "flagged": flagged}


def unchanged(cache, prior_path):
    """True if a prior cache exists with the same content hash (task set unchanged -> stay warm)."""
    if not os.path.exists(prior_path):
        return False
    try:
        with open(prior_path, encoding="utf-8") as f:
            return json.load(f).get("content_hash") == cache["content_hash"]
    except (OSError, json.JSONDecodeError):
        return False


# ── sweep freshness sidecar (A49) ────────────────────────────────────────────────────────────
# A degraded gather PRESERVES the prior warm cache (right — an empty cache blinds the morning brief).
# But a PERMANENTLY-misconfigured source degrades EVERY run, so the warm cache freezes forever while
# `generated_utc` (last good write) keeps looking recent-ish — the brief's dossiers-vs-sweep check
# can't see that the sweep itself stopped reaching reality, and the worklist silently freezes (the
# exact false-silent the resolve layer exists to kill). This sidecar records EVERY run's outcome so
# the brief can surface a loud staleness warning; it is metadata only and never touches sweep.json.
STATUS_FILE = "sweep-status.json"


def _read_status(cache_dir):
    try:
        with open(os.path.join(cache_dir, STATUS_FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_status(cache_dir, status):
    os.makedirs(cache_dir, exist_ok=True)
    tmp = os.path.join(cache_dir, STATUS_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(cache_dir, STATUS_FILE))


def run(env_root, tasks_file=None, now=None):
    """Do the sweep. Returns a result dict. status: skipped | warm | written."""
    now = now or _utcnow()
    cfg = resolve_config(env_root)
    if cfg is None or not cfg["keywords"]:
        return {"stage": STAGE, "status": "skipped", "reason": "resolve not configured", "ts": now}
    cache_dir = cfg["cache_dir"]
    cache_path = os.path.join(cache_dir, "sweep.json")
    prior = _read_status(cache_dir)
    tasks, source = gather_tasks(cfg, tasks_file)
    if source == "error":
        # a transient gather failure must NOT clobber a good warm cache — an empty cache would send
        # the morning brief blind on economic tasks (the exact A31 failure this task exists to fix).
        # BUT record the degradation (A49): a permanently-failing source would otherwise freeze the
        # warm cache forever with nothing telling the brief the sweep stopped reaching reality.
        consecutive = int(prior.get("consecutive_degraded") or 0) + 1
        # A60: a degraded sweep recomputes NO candidates -> preserve the stability counter rather
        # than reset it, or a transient blip would re-loudden a known-stable backlog for N more days.
        _write_status(cache_dir, {"last_attempt_utc": now, "last_source": source,
                                  "last_good_utc": prior.get("last_good_utc"),
                                  "consecutive_degraded": consecutive,
                                  "candidates_fingerprint": prior.get("candidates_fingerprint"),
                                  "candidates_unchanged_days": prior.get("candidates_unchanged_days")})
        return {"stage": STAGE, "status": "degraded", "source": source, "ts": now,
                "cache_path": cache_path, "consecutive_degraded": consecutive,
                "note": "gather failed; prior cache preserved"}
    cache = build_cache(tasks, cfg["keywords"], _entities(cfg["entities_dirs"]), source)
    cache["generated_utc"] = now
    warm = unchanged(cache, cache_path)
    if not warm:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = cache_path + ".tmp"           # atomic write — a crash mid-write can't corrupt the cache
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp, cache_path)
    # A60: track how many consecutive good sweeps have produced the SAME worklist shape (id+candidates)
    # so the brief can quiet a known-stable unresolved backlog; any real change resets it to 1.
    fp = _candidates_fingerprint(cache["flagged"])
    try:
        prior_days = int(prior.get("candidates_unchanged_days") or 0)
    except (TypeError, ValueError):
        prior_days = 0     # a corrupt sidecar value must not crash the sweep (mirror the reader's guard)
    unchanged_days = prior_days + 1 if fp == prior.get("candidates_fingerprint") else 1
    # a reached source (even if content unchanged -> warm) is a GOOD sweep: reset the degraded streak.
    _write_status(cache_dir, {"last_attempt_utc": now, "last_source": source,
                              "last_good_utc": now, "consecutive_degraded": 0,
                              "candidates_fingerprint": fp,
                              "candidates_unchanged_days": unchanged_days})
    return {"stage": STAGE, "status": "warm" if warm else "written", "source": source,
            "task_count": cache["task_count"], "flagged_count": cache["flagged_count"],
            "cache_path": cache_path, "ts": now}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Overnight resolve sweep: flag economic tasks + cache crosswalk candidate refs")
    ap.add_argument("--env-root", required=True)
    ap.add_argument("--tasks-file", help="JSON {tasks:[...]} override (hermetic runs/tests); default gathers from Notion task_views")
    ap.add_argument("--no-context-log", action="store_true", help="skip the context-log emit (tests)")
    args = ap.parse_args(argv)
    res = run(args.env_root, tasks_file=args.tasks_file)
    if not args.no_context_log and res.get("status") != "skipped":
        rec = {"ts": res["ts"], "stage": STAGE, "skill": "aios-resolve-sweep",
               "source": res.get("source"), "task_count": res.get("task_count", 0),
               "flagged_count": res.get("flagged_count", 0), "result": res["status"]}
        try:
            context_log.emit(rec, os.path.join(args.env_root, "state", "context-log.jsonl"))
        except Exception as e:
            res["context_log_warn"] = str(e)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
