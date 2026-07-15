"""Deterministic renderer: brief-cache item -> card markdown.

Pure stdlib. No LLM, no network. The card format lives HERE, not in skill prose,
so it cannot drift between renders or surfaces. See
docs/superpowers/specs/2026-07-02-brief-deterministic-card-render-design.md
"""
import json
import sys

VALID_GRADES = {"1", "2a", "2b"}

# Cache stores lowercase domain keys; the card shows display names. Fact-free: the engine
# knows only its own "kb" station — person-domain display names come from the cache's optional
# `domain_display` map (written at gather from the profile), else a Title Case fallback.
DOMAIN_DISPLAY = {"kb": "KB"}
_VOWELS = frozenset("aeiou")


def _pretty(word):
    # A short all-consonant slug reads as an acronym (gm, kb, hr) -> uppercase; any other word
    # (incl. short vowelled slugs like `fo`/`it`) title-cases. Generic (fact-free): fixes `gm` ->
    # `GM` without baking in instance names — exact names for ambiguous slugs come from the profile.
    return word.upper() if 0 < len(word) <= 3 and not (_VOWELS & set(word.lower())) else word.capitalize()


def _display(domain, display_map=None):
    if display_map and domain in display_map:
        return display_map[domain]
    if domain in DOMAIN_DISPLAY:
        return DOMAIN_DISPLAY[domain]
    return " ".join(_pretty(w) for w in domain.replace("_", " ").replace("-", " ").split()) or domain


def _voice(item, key):
    """Resolve system_voice/claude_voice from item-level (station items) or,
    failing that, nested under 'recommended' (Act-vs-Track items)."""
    v = item.get(key)
    if v is None and isinstance(item.get("recommended"), dict):
        v = item["recommended"].get(key)
    return v


def render_system_line(sv):
    """Blue line per grade. sv is None (Grade 0) or {grade, text, cite}."""
    if not sv or sv.get("grade") not in VALID_GRADES:
        return "— *your system is silent* —"
    grade = sv["grade"]
    text = sv.get("text", "")
    cite = sv.get("cite")
    if grade == "1":
        return f"🔵 **Your system says** *(Grade 1 — solid)*: {text} — cite: {cite}"
    if grade == "2a":
        return f"🔵 *Your system's logic implies* *(Grade 2a — precedent)*: {text} — by {cite}"
    # grade == "2b"
    rule = cite or "your principle"
    return f"🔵 *Loosely, by your {rule}* *(Grade 2b — principle)*: {text}"


def render_claude_line(cv):
    """Orange line — ALWAYS present."""
    text = (cv or {}).get("text", "")
    return f"🟠 **Claude**: {text}"


def _reframe_line(item):
    """The `↻ In motion` line for a thread-linked item — the thread's next_action IS what's true now,
    so it reframes the stale cached narrative. Returns the line or None (no linked thread)."""
    im = item.get("in_motion")
    if not isinstance(im, dict):
        return None
    na = (im.get("next_action") or "").strip()
    return f"↻ In motion — {na}" if na else "↻ In motion"


def render_card(item, display_map=None):
    """Full per-item card markdown. The two-layer block is ALWAYS present;
    optional context lines (urgency/playbook/flags) appear only when the item
    carries that data (station items are minimal; Act-vs-Track items are full)."""
    title = item.get("title") or "(untitled)"   # A49: .get — the live-gather path is unvalidated
    domain = item.get("domain", "")
    tag = _display(domain, display_map)
    header = f"**{title}**  [{tag}]" if domain else f"**{title}**"
    lines = [header]
    reframe = _reframe_line(item)
    if reframe:
        lines.append(f"- {reframe}")
    if item.get("urgency"):
        lines.append(f"- Urgency: {item['urgency']}")
    if item.get("your_playbook"):
        lines.append(f"- Your playbook: {item['your_playbook']}")
    if item.get("flags"):
        flags = item["flags"]
        flags_str = ", ".join(flags) if isinstance(flags, list) else str(flags)
        lines.append(f"- Flags: {flags_str}")
    lines.append("")
    lines.append(render_system_line(_voice(item, "system_voice")))
    lines.append(render_claude_line(_voice(item, "claude_voice")))
    return "\n".join(lines)


def _legacy_layers(item):
    """Tolerate the pre-A11 needs_you shape: recommended = [{"layer": "your_system"|"claude",
    "action": ...}, ...]. Returns (system_text, claude_text) or (None, None) when not that shape."""
    rec = item.get("recommended")
    if not isinstance(rec, list):
        return None, None
    sys_t = cla_t = None
    for entry in rec:
        if not isinstance(entry, dict):
            continue
        if entry.get("layer") == "your_system" and sys_t is None:
            sys_t = entry.get("action")
        elif entry.get("layer") == "claude" and cla_t is None:
            cla_t = entry.get("action")
    return sys_t, cla_t


def render_overview_row(item, display_map=None):
    """A11: compact Act-vs-Track overview row — header + urgency + the two-layer block as a
    blockquote. Same voices, same grading, same always-on Claude line as render_card; only the
    framing is compact (the Act list is a merged top-N, not the full station card). The legacy
    layers-list shape renders ungraded (it carries no grade/cite — never invent one)."""
    title = item.get("title") or "(untitled)"   # A49: .get — the live-gather path is unvalidated
    domain = item.get("domain", "")
    header = f"**{title}**  [{_display(domain, display_map)}]" if domain else f"**{title}**"
    lines = [header]
    reframe = _reframe_line(item)
    if reframe:
        lines.append(f"- {reframe}")
    if item.get("urgency"):
        lines.append(f"- Urgency: {item['urgency']}")
    sv, cv = _voice(item, "system_voice"), _voice(item, "claude_voice")
    if sv is None and cv is None:
        lsys, lcla = _legacy_layers(item)
        if lsys is not None or lcla is not None:
            lines.append(f"> 🔵 **Your system**: {lsys}" if lsys
                         else "> — *your system is silent* —")
            lines.append(f"> 🟠 **Claude**: {lcla or ''}")
            return "\n".join(lines)
    lines.append("> " + render_system_line(sv))
    lines.append("> " + render_claude_line(cv))
    return "\n".join(lines)


def _court(item):
    """The item's in-motion court ('you'/'others'/'done'), or None when it has no linked thread."""
    im = item.get("in_motion")
    return im.get("court") if isinstance(im, dict) else None


def render_overview(cache, limit=None):
    """The merged Act list from cache['needs_you'] — items that still need YOUR move: no linked
    thread, or an open one (court 'you'). Waiting ('others') and done ('done') items are routed
    out to render_in_motion. limit (optional int) caps the rows — the caller's '≈5 view-more' cut."""
    dm = cache.get("domain_display")
    # Act is the catch-all: only genuinely-routed courts (others/done) leave it, so an unknown or
    # malformed court degrades to visible-in-Act rather than vanishing from both surfaces.
    items = [i for i in (cache.get("needs_you") or []) if _court(i) not in ("others", "done")]
    if limit is not None:
        items = items[:int(limit)]
    return "\n\n".join(render_overview_row(i, dm) for i in items)


def render_in_motion(cache):
    """The ⏳ In-motion track for needs_you items with a linked thread: 'others' -> waiting-on-others
    (a compact awareness list, no A/B buttons — nothing to decide); 'done' (resolved/reverted) ->
    acknowledged as cleared, never left in Act and never mislabelled "waiting" (review finding #3).
    Nothing in either bucket -> ONE clean line, never an empty panel (matches render_settle)."""
    items = cache.get("needs_you") or []
    waiting = [i for i in items if _court(i) == "others"]
    done = [i for i in items if _court(i) == "done"]
    if not waiting and not done:
        return "⏳ In motion: nothing waiting"
    lines = []
    if waiting:
        lines.append("⏳ **In motion — waiting on others (not your move)**")
        lines.append("")
        for it in waiting:
            title = it.get("title") or "(untitled)"
            na = ((it.get("in_motion") or {}).get("next_action") or "").strip()
            lines.append(f"· {title} — {na}" if na else f"· {title}")
    if done:
        if lines:
            lines.append("")
        titles = ", ".join(it.get("title") or "(untitled)" for it in done)
        lines.append(f"✓ {len(done)} cleared by their thread (resolved): {titles}")
    return "\n".join(lines)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _station_items(cache, station):
    # Real cache shape: stations.<domain> is a list of item dicts directly.
    # Tolerate a {"items": [...]} wrapper defensively.
    node = cache.get("stations", {}).get(station)
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        return node.get("items", [])
    return []


def render_station(cache, station):
    dm = cache.get("domain_display")
    return "\n\n".join(render_card(i, dm) for i in _station_items(cache, station))


def render_card_by_id(cache, item_id):
    dm = cache.get("domain_display")
    for station in cache.get("stations", {}):
        for it in _station_items(cache, station):
            if it.get("item_id") == item_id or it.get("id") == item_id:
                return render_card(it, dm)
    raise KeyError(item_id)


def render_dossier(dossier):
    """Dossier dict -> verbatim resolve verdict card. The papered/conflict/verbal-only/silent
    distinction is Paper-Governs and MUST NOT drift between renders, so it is emitted here (lifted
    verbatim by the brief), never hand-written in skill prose. The verdict itself comes from
    resolve_verdict.compute_verdict — this only formats it."""
    title = dossier.get("title", "")
    verdict = dossier.get("verdict")
    header = "**%s**" % title
    if verdict == "papered":
        body = "🟢 **Papered** — %s" % (dossier.get("canonical") or "(figure cited)")
    elif verdict == "conflict":
        body = "🔴 **Conflict (held for you)** — %s" % (dossier.get("conflict") or "unresolved discrepancy")
    elif verdict == "verbal-only":
        prov = ", ".join(str(p) for p in (dossier.get("provenance") or []) if p is not None) or "verbal"
        body = "🟠 **Verbal only — no executed paper** (%s)" % prov
    elif verdict == "silent":
        body = "— *resolve silent (no aligned evidence)* —"
    else:
        body = "— *resolve verdict unavailable* —"
    return header + "\n" + body


def render_settle(cache, domains=None):
    """Stage-0 settle panel: auto-heal summary + candidates grouped by proposed_transition.
    Deterministic render (never hand-composed in prose) — the brief lifts this verbatim.
    Empty settle (no heals, no candidates) still renders a line, never an empty panel.

    `domains` (optional set/list of kb keys, e.g. {"dev"}) scope-filters the CANDIDATES to those
    silos only — a scoped brief must not leak other silos' settle candidates. `auto_healed` rows
    are ALWAYS shown regardless of `domains`: they are the user's own already-executed replays and
    carry no domain. Falsy/empty `domains` means no filter (root/all — unchanged behavior)."""
    s = (cache or {}).get("settle") or {}
    healed = s.get("auto_healed") or []
    cands = s.get("candidates") or []
    if domains:
        allowed = set(domains)
        cands = [c for c in cands if c.get("domain") in allowed]
    if not healed and not cands:
        return "**Stage 0 — Settle:** clear ✓ — nothing to settle."
    lines = ["**Stage 0 — Settle**", ""]
    for h in healed:
        lines.append(f"✅ Healed: {h.get('title')} → {h.get('to')}")
    if healed:
        lines.append("")
    groups = {}
    for c in cands:
        groups.setdefault(c.get("proposed_transition"), []).append(c.get("title"))
    for tr, titles in groups.items():
        sample = ", ".join(f'"{t}"' for t in titles[:3])
        lines.append(f"▸ {len(titles)}× → {tr}   e.g. {sample}    [Confirm all] [Expand]")
    return "\n".join(lines)


def render_factory_health(latest_md):
    """One-line factory-health status lifted verbatim into the brief (GM2). `latest_md` is the
    digest markdown content (or None if absent). Counts finding lines (`- **[`); 0/absent => clear.
    Deterministic render — never hand-composed in skill prose."""
    n = sum(1 for ln in (latest_md or "").splitlines() if ln.startswith("- **["))
    if n == 0:
        return "🔧 **Factory health:** clear ✓"
    return f"🔧 **Factory health:** {n} open — see `state/factory-health/latest.md`"


def render_factory_standup(data):
    """Deterministic Factory Standup panel (brief Dev slice). Pure function of standup.json.
    Empty state renders ONE clean line, never an empty panel (matches render_settle)."""
    g = data.get("groups", {})
    errs = data.get("errors", [])
    sp = data.get("spend") or {}
    has_spend = bool(sp.get("output_tokens") or sp.get("cost_usd"))
    acc0 = data.get("acceptance") or {}
    has_acc = bool(acc0.get("factory") or acc0.get("gate"))
    if not any(g.get(k) for k in ("veto", "needs_you", "handed_off", "stuck")) and not errs \
            and not has_spend and not has_acc:
        return "🏭 Factory Standup — nothing waiting (backlogs drained clean)."
    lines = ["🏭 Factory Standup"]
    def _emit(sym, label, items, fmt):
        if items:
            lines.append(f"  {sym} {label} ({len(items)}):")
            for it in items:
                lines.append("    - " + fmt(it))
    _emit("✅", "veto window", g.get("veto", []),
          lambda it: f"{it.get('repo','?')} {it.get('id','?')} — {it.get('title') or '(untitled)'} (shipped {it.get('date','?')}; VETO)")
    _emit("⚠", "needs you — decide", g.get("needs_you", []),
          lambda it: f"{it.get('repo','?')} {it.get('id','?')} — {it.get('title') or '(untitled)'} ({it.get('reason','?')})")
    _emit("↪", "handed off", g.get("handed_off", []),
          lambda it: f"{it.get('repo','?')} {it.get('id','?')} — {it.get('title') or '(untitled)'}")
    _emit("✖", "stuck", g.get("stuck", []),
          lambda it: f"{it.get('repo','?')} {it.get('id','?')} — {it.get('title') or '(untitled)'} [{it.get('reason','?')}]")
    _emit("‼", "backlog parse errors", errs,
          lambda e: f"{e.get('repo','?')} — {e.get('error','?')}")
    if has_spend:  # H62: rolling unattended-tier token/$ spend + fail-loud soft-cap flag
        tok, cost, cap = sp.get("output_tokens", 0), sp.get("cost_usd", 0.0), sp.get("cap") or 0
        line = f"  💸 unattended spend today: {tok:,} out-tok"
        if cost:
            line += f" / ${cost:,.2f}"
        if cap:
            line += f" (soft cap {cap:,})"
        lines.append(line + (" ⚠ OVER SOFT-CAP" if sp.get("over_cap") else ""))
    acc = data.get("acceptance") or {}
    fa, ga = acc.get("factory"), acc.get("gate")
    if fa:
        w = acc.get("window_days", 30)
        line = (f"  📊 factory acceptance ({w}d): {fa.get('accepted', 0)} shipped / "
                f"{fa.get('reverted', 0)} reverted / {fa.get('unknown_sha', 0)} unknown-sha")
        if fa.get("usd_per_accepted") is not None:
            line += f" → ${fa['usd_per_accepted']:,.2f}/accepted"
        lines.append(line)
        if fa.get("reverted_ids"):
            lines.append("     reverted: " + ", ".join(fa["reverted_ids"]))
    if ga:
        w = acc.get("window_days", 30)
        if "note" in ga:
            lines.append(f"  📊 gate acceptance: unavailable — {ga['note']}")
        elif ga.get("n"):
            pct = round(100 * ga.get("accepted", 0) / ga["n"])
            line = f"  📊 gate acceptance ({w}d): {pct}% ({ga.get('accepted', 0)}/{ga['n']})"
            if ga.get("usd_per_accepted") is not None:
                line += f" · ${ga['usd_per_accepted']:,.2f}/accepted"
            lines.append(line)
    return "\n".join(lines)


def _extract_domain_filters(argv):
    """Pull repeatable `--domain <kb>` flags out of argv (settle's scope filter),
    returning (remaining_argv, domains). Order-independent; matches the flag's
    documented usage as trailing options after the positional args."""
    remaining, domains = [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--domain" and i + 1 < len(argv):
            domains.append(argv[i + 1])
            i += 2
            continue
        remaining.append(argv[i])
        i += 1
    return remaining, domains


def main(argv):
    # The card emits non-cp1252 glyphs (🔵/🟠). Windows stdout defaults to cp1252
    # and would crash on print(); force UTF-8 on the CLI output boundary.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    argv, domain_filters = _extract_domain_filters(argv)
    if len(argv) < 3:
        print("usage: brief_render.py {station|card} <cache.json> <station|item_id>\n"
              "       brief_render.py overview <cache.json> [limit]\n"
              "       brief_render.py in-motion <cache.json>\n"
              "       brief_render.py settle <cache.json> [--domain <kb> ...]\n"
              "       brief_render.py factory-health <latest.md path>",
              file=sys.stderr)
        return 2
    op, cache_path = argv[1], argv[2]
    key = argv[3] if len(argv) > 3 else None
    # arg validation BEFORE the cache load, so usage errors stay exit-2 usage errors
    if op in ("station", "card") and not key:
        print(f"usage: brief_render.py {op} <cache.json> <{'station' if op == 'station' else 'item_id'}>",
              file=sys.stderr)
        return 2
    limit = None
    if op == "overview" and key is not None:
        try:
            limit = max(0, int(key))
        except ValueError:
            print(f"overview limit must be an integer, got {key!r}", file=sys.stderr)
            return 2
    if op == "factory-health":
        md = None
        try:
            with open(cache_path, encoding="utf-8") as f:
                md = f.read()
        except OSError:
            md = None
        print(render_factory_health(md))
        return 0
    cache = _load(cache_path)
    if op == "station":
        print(render_station(cache, key))
    elif op == "card":
        print(render_card_by_id(cache, key))
    elif op == "overview":
        print(render_overview(cache, limit=limit))
    elif op == "in-motion":
        print(render_in_motion(cache))
    elif op == "settle":
        print(render_settle(cache, domains=domain_filters or None))
    else:
        print(f"unknown op {op!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
