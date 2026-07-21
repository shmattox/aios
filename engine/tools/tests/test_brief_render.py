# sanitize:allow-file — fixtures use synthetic/out-of-range ids by design (A79)
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brief_render as R  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "brief-cache.sample.json")


# --- Task 1: voice lines + resolver ---

def test_system_line_grade1():
    sv = {"grade": "1", "text": "Ship it.", "cite": "decisions.md#x"}
    assert R.render_system_line(sv) == \
        "🔵 **Your system says** *(Grade 1 — solid)*: Ship it. — cite: decisions.md#x"


def test_system_line_grade2a():
    sv = {"grade": "2a", "text": "Probably hold.", "cite": "your 2026-06 call"}
    assert R.render_system_line(sv) == \
        "🔵 *Your system's logic implies* *(Grade 2a — precedent)*: Probably hold. — by your 2026-06 call"


def test_system_line_grade2b_uses_cite_as_rule():
    sv = {"grade": "2b", "text": "Lean conservative.", "cite": "Paper-Governs"}
    assert R.render_system_line(sv) == \
        "🔵 *Loosely, by your Paper-Governs* *(Grade 2b — principle)*: Lean conservative."


def test_system_line_grade0_and_null_are_silent():
    assert R.render_system_line(None) == "— *your system is silent* —"
    assert R.render_system_line({"grade": None}) == "— *your system is silent* —"


def test_claude_line_always_present():
    assert R.render_claude_line({"text": "Industry default is X."}) == \
        "🟠 **Claude**: Industry default is X."


def test_voice_resolves_item_level_then_recommended():
    station_item = {"system_voice": {"grade": "1", "text": "a", "cite": "c"}}
    assert R._voice(station_item, "system_voice")["text"] == "a"
    act_item = {"recommended": {"system_voice": {"grade": "1", "text": "b", "cite": "c"}}}
    assert R._voice(act_item, "system_voice")["text"] == "b"
    assert R._voice({}, "system_voice") is None


# --- Task 2: render_card ---

def test_render_card_station_item_minimal():
    item = {
        "item_id": "sys-1", "title": "Fix the sorter taxonomy", "domain": "System",
        "system_voice": {"grade": "1", "text": "Hold, then re-drive.", "cite": "decisions.md#taxo"},
        "claude_voice": {"text": "Batch the 8 stubs."},
    }
    out = R.render_card(item)
    assert out.splitlines()[0] == "**Fix the sorter taxonomy**  [System]"
    assert "🔵 **Your system says**" in out
    assert out.strip().endswith("🟠 **Claude**: Batch the 8 stubs.")
    assert "Urgency:" not in out


def test_render_card_act_item_full_and_nested_voice():
    item = {
        "id": "fo-2", "title": "Refi decision", "domain": "Family Office",
        "urgency": "closes Fri", "your_playbook": "sale leads, nothing locked",
        "flags": ["Paper-Governs"],
        "recommended": {
            "system_voice": {"grade": "2a", "text": "Wait.", "cite": "your May call"},
            "claude_voice": {"text": "Lock the rate."},
        },
    }
    out = R.render_card(item)
    assert "**Refi decision**  [Family Office]" in out
    assert "- Urgency: closes Fri" in out
    assert "- Your playbook: sale leads, nothing locked" in out
    assert "- Flags: Paper-Governs" in out
    assert "🔵 *Your system's logic implies*" in out
    assert "🟠 **Claude**: Lock the rate." in out


def test_render_card_maps_lowercase_domain_key_to_display_name():
    # fact-free (A26): pretty names come from the cache's domain_display map...
    item = {"title": "T", "domain": "familyoffice", "system_voice": None,
            "claude_voice": {"text": "c"}}
    assert R.render_card(item, {"familyoffice": "Family Office"}).splitlines()[0] \
        == "**T**  [Family Office]"
    # ...with a Title Case fallback when no map is present
    assert R.render_card(item).splitlines()[0] == "**T**  [Familyoffice]"
    # already-display values pass through unchanged
    item2 = {"title": "U", "domain": "Family Office", "system_voice": None,
             "claude_voice": {"text": "c"}}
    assert R.render_card(item2).splitlines()[0] == "**U**  [Family Office]"


def test_display_acronymizes_short_consonant_domain_keys():
    # A42 (fact-free): a short all-consonant slug reads as an acronym -> uppercase (`gm` -> `GM`),
    # fixing the `Gm` title-case leak; vowelled/long words still title-case; profile map still wins.
    assert R._display("gm") == "GM"                   # all-consonant slug -> acronym (the leak fix)
    assert R._display("kb") == "KB"
    assert R._display("hr") == "HR"
    assert R._display("dev") == "Dev"                 # has a vowel -> title-case (unchanged)
    assert R._display("fo") == "Fo"                   # vowel present -> title-case; profile map sets "FO"
    assert R._display("familyoffice") == "Familyoffice"
    assert R._display("family_office") == "Family Office"
    assert R._display("gm", {"gm": "GM Ventures"}) == "GM Ventures"   # profile display_map wins


def test_render_card_silent_system_still_has_claude():
    item = {"title": "T", "domain": "Dev", "system_voice": None,
            "claude_voice": {"text": "c"}}
    out = R.render_card(item)
    assert "— *your system is silent* —" in out
    assert "🟠 **Claude**: c" in out


# --- Task 3: CLI + cache walking ---

def test_render_station_emits_all_cards():
    cache = R._load(FIX)
    out = R.render_station(cache, "system")
    assert "**Fix sorter taxonomy**  [System]" in out
    assert "🔵 **Your system says**" in out
    assert "🟠 **Claude**: Batch the 8 stubs." in out


def test_render_card_by_id_found_and_missing():
    import pytest
    cache = R._load(FIX)
    assert "Renderer build" in R.render_card_by_id(cache, "dev-1")
    with pytest.raises(KeyError):
        R.render_card_by_id(cache, "nope")


def test_render_station_is_byte_stable():
    cache = R._load(FIX)
    a = R.render_station(cache, "system")
    b = R.render_station(cache, "system")
    assert a == b


def test_fixture_matches_real_cache_shape():
    """Guard against shape drift: the fixture must be a cache validate_cache accepts,
    and render_station must actually emit cards from it (not silently return '')."""
    import brief_session as B
    cache = R._load(FIX)
    ok, errs = B.validate_cache(cache)
    assert ok, errs
    assert R.render_station(cache, "system").strip() != ""


def test_fixture_chips_are_the_computed_ones_not_hand_typed():
    """The fixture is the reference for what a real cache looks like — if ITS chips were
    hand-typed they would model the exact habit the assertion exists to kill."""
    cache = R._load(FIX)
    assert cache["headline_bubbles"] == R.compute_headline_bubbles(cache)


def test_cli_headline_op_emits_the_computed_chips():
    """The chips rule ("never hand-typed") pointed at a Python function with no way to call
    it — so a model followed the cache-contract prose instead and typed "5 need you" over an
    act[] of 7. This op is how a cache-writer actually obtains them."""
    import subprocess
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_render.py")
    proc = subprocess.run([sys.executable, tool, "headline", FIX],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    emitted = json.loads(proc.stdout)
    assert emitted == R.compute_headline_bubbles(R._load(FIX))
    assert emitted[0] == "1 need you"          # the fixture's act[] holds exactly 1 item


def test_cli_headline_op_accepts_a_standup_and_adds_its_chip():
    import subprocess, tempfile
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_render.py")
    sp = os.path.join(tempfile.mkdtemp(), "standup.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump({"totals": {"needs_you": 21}, "delta": [{"id": "H54"}, {"id": "A69"}]}, f)
    proc = subprocess.run([sys.executable, tool, "headline", FIX, sp],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)[-1] == "21 decisions · 2 new"


def test_cli_headline_output_is_accepted_by_validate_cache():
    """End-to-end of the intended workflow: run the op, splice its output into the cache,
    validate. If these two ever disagree on format, the op is useless."""
    import subprocess
    import brief_session as B
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_render.py")
    proc = subprocess.run([sys.executable, tool, "headline", FIX],
                          capture_output=True, encoding="utf-8", errors="replace")
    cache = dict(R._load(FIX), headline_bubbles=json.loads(proc.stdout))
    ok, errs = B.validate_cache(cache)
    assert ok, errs


def test_cli_station_does_not_crash_on_windows_stdout():
    """Regression: the card emits 🔵/🟠; Windows stdout defaults to cp1252 and
    would crash on print(). The CLI must force UTF-8 and exit 0."""
    import subprocess
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_render.py")
    proc = subprocess.run(
        [sys.executable, tool, "station", FIX, "system"],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "🔵".encode("utf-8") in proc.stdout


# --- A11: Act-vs-Track overview through the renderer ---

ACT_ITEM = {
    "id": "OI-901", "title": "Pay the Bayview taxes", "domain": "familyoffice",
    "urgency": "due 2026-07-03, 2 days ago",
    "recommended": {
        "system_voice": {"grade": "1", "text": "Pay the 2024 bill.", "cite": "Decision #34"},
        "claude_voice": {"text": "Wire cutoffs are 17:00 ET."},
    },
}


def test_overview_row_compact_two_layer_blockquote():
    out = R.render_overview_row(ACT_ITEM)
    lines = out.splitlines()
    assert lines[0] == "**Pay the Bayview taxes**  [Familyoffice]"
    assert lines[1] == "- Urgency: due 2026-07-03, 2 days ago"
    assert lines[2].startswith("> 🔵 **Your system says**") and "Decision #34" in lines[2]
    assert lines[3] == "> 🟠 **Claude**: Wire cutoffs are 17:00 ET."


def test_overview_row_silent_system_still_carries_claude():
    item = {"title": "T", "recommended": {"claude_voice": {"text": "x"}}}
    out = R.render_overview_row(item)
    assert "> — *your system is silent* —" in out
    assert "> 🟠 **Claude**: x" in out


def test_overview_uses_domain_display_map():
    out = R.render_overview({"domain_display": {"familyoffice": "Family Office"},
                             "act": [ACT_ITEM]})
    assert "[Family Office]" in out


def test_overview_reads_act_not_needs_you():
    cache = {"act": [{"title": "T", "domain": "gm",
                      "claude_voice": {"text": "c"}, "system_voice": None}]}
    out = R.render_overview(cache)
    assert "T" in out, "render_overview must read cache['act']"


def test_standup_needs_you_is_untouched_by_the_rename():
    # groups.needs_you is the STANDUP's decision queue — a different object that keeps its name.
    cache = {"groups": {"veto": [], "needs_you": [{"repo": "r", "id": "H1", "title": "X",
                                                  "reason": "economic/ownership"}],
                        "handed_off": [], "stuck": []}}
    out = R.render_factory_standup(cache)
    assert "needs you — decide" in out and "X" in out


def test_overview_limit_and_empty():
    cache = {"act": [dict(ACT_ITEM, id=f"i{i}", title=f"t{i}") for i in range(4)]}
    assert R.render_overview(cache, limit=2).count("🟠") == 2
    assert R.render_overview({}) == ""


def test_overview_legacy_layers_list_shape_still_two_layer():
    # the pre-A11 live-cache shape: recommended is a LIST of {layer, action}
    item = {"title": "Pay taxes", "domain": "familyoffice",
            "recommended": [
                {"layer": "your_system", "action": "Confirm the payment posts Monday."},
                {"layer": "claude", "action": "Get the paper into Drive same-day."}]}
    out = R.render_overview_row(item)
    assert "> 🔵 **Your system**: Confirm the payment posts Monday." in out
    assert "> 🟠 **Claude**: Get the paper into Drive same-day." in out


def test_overview_legacy_layers_missing_system_is_silent_not_fabricated():
    item = {"title": "T", "recommended": [{"layer": "claude", "action": "x"}]}
    out = R.render_overview_row(item)
    assert "> — *your system is silent* —" in out
    assert "> 🟠 **Claude**: x" in out


def test_cli_overview_exits_zero_even_without_act():
    import subprocess
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_render.py")
    proc = subprocess.run([sys.executable, tool, "overview", FIX], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


# --- C3: render_settle (Stage-0 settle panel) ---

def test_render_settle_groups_and_heals():
    cache = {"settle": {
        "auto_healed": [{"item_id": "OI-901", "title": "Pay tax", "to": "Done"}],
        "candidates": [
            {"task_id": "S1", "title": "SEAMS 1065", "proposed_transition": "in_progress"},
            {"task_id": "S2", "title": "Ship page", "proposed_transition": "done"},
            {"task_id": "S3", "title": "Call vendor", "proposed_transition": "done"},
        ]}}
    out = R.render_settle(cache)
    assert "Healed" in out and "Pay tax" in out          # auto-heal reported
    assert "2× → done" in out                            # two 'done' candidates grouped
    assert "SEAMS 1065" in out                            # candidate surfaced


def test_render_settle_empty_is_clear():
    out = R.render_settle({"settle": {"auto_healed": [], "candidates": []}})
    assert "clear" in out.lower() or "nothing to settle" in out.lower()


def test_render_settle_domain_filter_scopes_candidates():
    """Regression: a scoped brief (e.g. dev) must not leak other silos' settle candidates."""
    cache = {"settle": {
        "auto_healed": [{"item_id": "OI-901", "title": "Pay tax", "to": "Done"}],
        "candidates": [
            {"task_id": "D1", "title": "Renderer build", "proposed_transition": "in_progress",
             "domain": "dev"},
            {"task_id": "F1", "title": "SEAMS 1065", "proposed_transition": "done",
             "domain": "familyoffice"},
        ]}}
    scoped = R.render_settle(cache, domains={"dev"})
    assert "Renderer build" in scoped
    assert "SEAMS 1065" not in scoped
    assert "Healed" in scoped and "Pay tax" in scoped  # auto_healed always shown

    unscoped = R.render_settle(cache)
    assert "Renderer build" in unscoped and "SEAMS 1065" in unscoped


# --- GM2 Task 5: render_factory_health ---

def test_render_factory_health_counts_findings():
    md = "# Factory health\n\n- **[high] failed-run** — x\n- **[medium] recurring-failure** — y\n"
    assert R.render_factory_health(md) == "🔧 **Factory health:** 2 open — see `state/factory-health/latest.md`"


def test_render_factory_health_clear_when_absent_or_healthy():
    assert R.render_factory_health(None) == "🔧 **Factory health:** clear ✓"
    assert R.render_factory_health("# Factory health\n\n✅ No open loops-not-closing — healthy.\n") == "🔧 **Factory health:** clear ✓"


# --- Plan C Task 2: render_factory_standup (Dev-slice panel) ---

def test_render_factory_standup_groups():
    data = {"generated":"2026-07-11","groups":{
        "veto":[{"repo":"claude-env","id":"H20","title":"retire backup path","date":"2026-07-11"}],
        "needs_you":[{"repo":"claude-env","id":"H19","title":"SSOT flip","reason":"hard-to-reverse / [GATE: human]"}],
        "handed_off":[{"repo":"claude-env","id":"H30","title":"cowork re-sync"}],
        "stuck":[{"repo":"aios","id":"H31","title":"ordinary work","reason":"pytest import error"}]},
      "totals":{"veto":1,"needs_you":1,"handed_off":1,"stuck":1}}
    out = R.render_factory_standup(data)
    assert "Factory Standup" in out
    assert "✅" in out and "H20" in out and "VETO" in out.upper()
    assert "⚠" in out and "H19" in out
    assert "↪" in out and "H30" in out
    assert "✖" in out and "H31" in out and "pytest import error" in out


def test_render_factory_standup_empty_is_one_clean_line():
    data = {"generated":"2026-07-11","groups":{"veto":[],"needs_you":[],"handed_off":[],"stuck":[]},
            "totals":{"veto":0,"needs_you":0,"handed_off":0,"stuck":0}}
    out = R.render_factory_standup(data)
    assert "Factory Standup" in out and "nothing waiting" in out.lower()
    assert "\n\n" not in out.strip()      # a single clean line, never an empty multi-panel


def test_render_factory_standup_renders_spend_line():
    # H62: the rolling unattended-tier spend figure appears in the Factory panel
    data = {"groups": {"veto": [], "needs_you": [], "handed_off": [], "stuck": []},
            "totals": {"veto": 0, "needs_you": 0, "handed_off": 0, "stuck": 0},
            "spend": {"output_tokens": 425000, "cost_usd": 5.75, "cap": 8_000_000, "over_cap": False}}
    out = R.render_factory_standup(data)
    assert "425,000" in out and "$5.75" in out and "soft cap 8,000,000" in out
    assert "OVER SOFT-CAP" not in out           # under cap -> no alarm


def test_render_factory_standup_spend_over_cap_alarms():
    # H62 (3): an over-budget window renders the fail-loud flag (no kill — display only)
    data = {"groups": {"veto": [], "needs_you": [], "handed_off": [], "stuck": []},
            "totals": {"veto": 0, "needs_you": 0, "handed_off": 0, "stuck": 0},
            "spend": {"output_tokens": 9_000_000, "cost_usd": 130.0, "cap": 8_000_000, "over_cap": True}}
    out = R.render_factory_standup(data)
    assert "OVER SOFT-CAP" in out and "⚠" in out


def test_render_factory_standup_tolerates_missing_item_fields():
    data = {"groups": {"veto": [{"repo": "r", "id": "X1"}],   # no title, no date
                       "needs_you": [], "handed_off": [], "stuck": []},
            "totals": {"veto": 1, "needs_you": 0, "handed_off": 0, "stuck": 0}}
    out = R.render_factory_standup(data)            # must not raise
    assert "X1" in out and "(untitled)" in out


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))


# --- Thread reconciliation: in_motion partition + reframe (2026-07-11) ---

def _cache_in_motion():
    return {
        "act": [
            {"id": "OI-1000", "title": "NOTICE-A Northwind", "domain": "familyoffice",
             "urgency": "26d overdue", "claude_voice": {"text": "pull transcript"},
             "in_motion": {"thread_id": "OI-1000", "status": "open", "court": "you",
                           "next_action": "pull the IRS account transcript (EIN XX-XXXXXXX)"}},
            {"id": "OI-997", "title": "Acme extension", "domain": "familyoffice",
             "urgency": "28d overdue, awaiting send", "claude_voice": {"text": "x"},
             "in_motion": {"thread_id": "acme-loan-extension", "status": "parked", "court": "others",
                           "next_action": "DocuSigned; awaiting Sam's signature"}},
            {"id": "OI-958", "title": "Contoso Staking dissolve", "domain": "familyoffice",
             "urgency": "43d overdue", "claude_voice": {"text": "decide"}},
        ],
    }


def test_render_overview_keeps_act_items_only():
    # Act = no in_motion OR court == "you"; the parked (court=others) item is excluded
    out = R.render_overview(_cache_in_motion())
    assert "NOTICE-A Northwind" in out          # court you -> stays
    assert "Contoso Staking dissolve" in out  # no in_motion -> stays
    assert "Acme extension" not in out       # court others -> moved to in-motion track


def test_render_overview_row_open_thread_shows_reframe_line():
    out = R.render_overview(_cache_in_motion())
    assert "↻ In motion — pull the IRS account transcript (EIN XX-XXXXXXX)" in out


def test_render_in_motion_lists_waiting_items():
    out = R.render_in_motion(_cache_in_motion())
    assert "In motion" in out
    assert "· Acme extension — DocuSigned; awaiting Sam's signature" in out
    # Act items are NOT in the waiting track
    assert "NOTICE-A Northwind" not in out


def test_render_in_motion_empty_is_one_clean_line():
    out = R.render_in_motion({"act": [{"id": "x", "title": "t", "domain": "d",
                                       "claude_voice": {"text": "c"}}]})
    assert out == "⏳ In motion: nothing waiting"


def test_render_card_shows_reframe_line_when_in_motion():
    item = {"id": "OI-997", "title": "Acme", "domain": "familyoffice",
            "claude_voice": {"text": "x"},
            "in_motion": {"thread_id": "acme-loan-extension", "status": "parked",
                          "court": "others", "next_action": "awaiting signature"}}
    out = R.render_card(item)
    assert "↻ In motion — awaiting signature" in out


def test_render_card_unchanged_without_in_motion():
    item = {"title": "T", "domain": "d", "system_voice": None, "claude_voice": {"text": "c"}}
    assert "↻" not in R.render_card(item)


def test_render_resolved_item_not_in_waiting_track_and_not_in_act():
    # a resolved thread (court "done") is neither in Act nor labelled "waiting on others" (#3)
    cache = {"act": [
        {"id": "OI-1032", "title": "STRC done", "domain": "familyoffice",
         "claude_voice": {"text": "x"},
         "in_motion": {"thread_id": "OI-1032", "status": "resolved", "court": "done",
                       "next_action": "done"}},
    ]}
    act = R.render_overview(cache)
    inm = R.render_in_motion(cache)
    assert "STRC done" not in act                      # not an actionable Act row
    assert "waiting on others" not in inm.lower() or "STRC done" not in inm.split("resolved")[0]
    assert "cleared by their thread" in inm            # acknowledged, not silently dropped


def test_render_overview_unknown_court_degrades_to_visible_in_act():
    # defense-in-depth: an unrecognized court must NOT vanish from both surfaces — Act is the catch-all
    cache = {"act": [
        {"id": "OI-901", "title": "Weird court", "domain": "d", "claude_voice": {"text": "x"},
         "in_motion": {"thread_id": "t", "status": "?", "court": "foo", "next_action": "n"}},
    ]}
    assert "Weird court" in R.render_overview(cache)
    assert "Weird court" not in R.render_in_motion(cache)

def test_render_factory_standup_surfaces_parse_errors():
    # H56.4: a backlog parse error alone still renders the panel (not "nothing waiting")
    data = {"groups": {"veto": [], "needs_you": [], "handed_off": [], "stuck": []},
            "totals": {"veto": 0, "needs_you": 0, "handed_off": 0, "stuck": 0},
            "errors": [{"repo": "aios", "backlog": "x/BACKLOG.md", "error": "ValueError: boom"}]}
    out = R.render_factory_standup(data)
    assert "Factory Standup" in out and "parse errors" in out and "boom" in out
    assert "nothing waiting" not in out


# --- A73 Task 5: standup panel renders factory+gate acceptance ---

def test_standup_renders_acceptance_lines():
    data = {"groups": {}, "errors": [], "spend": {},
            "acceptance": {"window_days": 30,
                           "factory": {"accepted": 12, "reverted": 1, "unknown_sha": 2,
                                       "spend_usd": 49.4, "usd_per_accepted": 4.12,
                                       "reverted_ids": ["A65"]},
                           "gate": {"n": 123, "accepted": 113, "rejected": 8, "reverted": 2,
                                    "spend_usd": 68.9, "usd_per_accepted": 0.61}}}
    out = R.render_factory_standup(data)
    assert "📊 factory acceptance (30d): 12 shipped / 1 reverted / 2 unknown-sha → $4.12/accepted" in out
    assert "reverted: A65" in out
    # H90 leg 2: throughput (accepted) now renders beside its counter-metrics (rejects + reverts).
    assert "📊 gate acceptance (30d): 92% (113/123) · 8 rejected · 2 reverted · $0.61/accepted" in out


def test_standup_gate_counters_omitted_when_zero():
    # H90 leg 2: a clean gate (no rejects/reverts) renders the throughput alone — no empty counters.
    data = {"groups": {}, "errors": [], "spend": {},
            "acceptance": {"window_days": 30,
                           "gate": {"n": 10, "accepted": 10, "rejected": 0, "reverted": 0}}}
    out = R.render_factory_standup(data)
    assert "📊 gate acceptance (30d): 100% (10/10)" in out
    assert "rejected" not in out and "reverted" not in out


def test_standup_renders_runtime_drift_line():
    # H74 leg 2: a stale INSTALLED engine (folded from the factory-gate emit sidecar) surfaces.
    data = {"groups": {}, "errors": [], "spend": {}, "acceptance": {},
            "runtime_drift": {"line": "⚙ runtime drift: installed v0.8.0 ≠ repo v0.9.0"}}
    out = R.render_factory_standup(data)
    assert "runtime drift" in out and "0.8.0" in out
    # drift alone must NOT collapse to the empty "nothing waiting" line
    assert "nothing waiting" not in out


def test_standup_acceptance_gate_note_renders_loud():
    data = {"groups": {}, "errors": [], "spend": {},
            "acceptance": {"window_days": 30,
                           "factory": {"accepted": 0, "reverted": 0, "unknown_sha": 0,
                                       "spend_usd": 0.0, "usd_per_accepted": None, "reverted_ids": []},
                           "gate": {"note": "gate metrics JSON absent — run gate_metrics.py report"}}}
    out = R.render_factory_standup(data)
    assert "gate acceptance: unavailable — gate metrics JSON absent" in out


def test_standup_empty_state_line_unchanged_without_acceptance():
    out = R.render_factory_standup({"groups": {}, "errors": [], "spend": {}})
    assert out == "🏭 Factory Standup — nothing waiting (backlogs drained clean)."


# --- Task 8: headline_bubbles computed — the 5/7/21 regression ---

def test_headline_bubbles_are_computed_from_the_objects_they_count():
    # The 5/7/21 regression: on 2026-07-15 the cache said "5 need you" in prose while act had 7
    # items and the standup said 21. A computed chip cannot disagree with its own list.
    cache = {"act": [{"title": "a%d" % i, "domain": "gm",
                      "claude_voice": {"text": "c"}, "system_voice": None} for i in range(7)],
             "held": [{"id": "h1"}, {"id": "h2"}],
             "flags": ["f1"], "going_quiet": [{"name": "X"}],
             "settle": {"auto_healed": [], "candidates": []}}
    bubbles = R.compute_headline_bubbles(cache)
    assert bubbles[0] == "7 need you", bubbles
    assert "2 to review" in bubbles
    assert "1 Paper-Governs flag" in bubbles or "1 Paper-Governs flags" in bubbles
    assert "1 going quiet" in bubbles


def test_headline_bubbles_cannot_disagree_with_act():
    cache = {"act": [], "held": [], "flags": [], "going_quiet": [],
             "settle": {"auto_healed": [], "candidates": []}}
    assert R.compute_headline_bubbles(cache)[0] == "0 need you"


# --- Task 9: render_unchanged_line — the quiet "N unchanged" line ---

def test_unchanged_line_is_quiet_and_countable():
    assert R.render_unchanged_line({"unchanged": [{"id": "H1"}, {"id": "H2"}]}) \
        == "· 2 unchanged · walk them"
    assert R.render_unchanged_line({"unchanged": []}) == ""


# --- FIX 1: the chip counts the RENDERED (court-filtered) Act rows, not len(act) ---

def _seven_act_two_waiting():
    """7 raw act items; 2 route to the ⏳In-motion track (court others/done) so only 5 render as
    Act rows — the exact live 2026-07-15 shape the 'corrected' 5/7/21 finding describes."""
    act = [{"id": "you-%d" % i, "title": "you%d" % i, "domain": "gm",
            "claude_voice": {"text": "c"}, "system_voice": None} for i in range(5)]
    act.append({"id": "w1", "title": "waiting1", "domain": "gm", "claude_voice": {"text": "c"},
                "system_voice": None, "in_motion": {"court": "others", "next_action": "sig"}})
    act.append({"id": "w2", "title": "waiting2", "domain": "gm", "claude_voice": {"text": "c"},
                "system_voice": None, "in_motion": {"court": "done"}})
    return act


def test_headline_chip_counts_rendered_act_rows_not_len_act():
    # CORRECTED 5/7/21: raw act=7, but 2 are court-filtered to In-motion, so 5 render as Act. The
    # chip must count what the reader SEES ("5 need you"), NOT len(act) ("7"). Before the _act_rows
    # fix, compute_headline_bubbles returned "7 need you" over a 5-row list.
    act = _seven_act_two_waiting()
    cache = {"act": act, "held": [], "flags": [], "going_quiet": [],
             "settle": {"auto_healed": [], "candidates": []}}
    assert len(act) == 7                       # raw list still holds 7
    assert len(R._act_rows(cache)) == 5        # but only 5 render as Act rows
    assert R.compute_headline_bubbles(cache)[0] == "5 need you"
    # the chip's count equals the rows render_overview actually emits (the load-bearing sync)
    rendered = R.render_overview(cache)
    assert "waiting1" not in rendered and "waiting2" not in rendered
    assert "waiting1" in R.render_in_motion(cache)  # the 2 are shown, in the In-motion track


# --- FIX 3 (A8): render_unchanged_line reachable via a CLI op ---

def test_cli_unchanged_op_round_trips_the_unchanged_line():
    # A8: gather.md tells the model to "Lift render_unchanged_line(standup) verbatim", but the
    # function had no CLI op, so the count got hand-typed. This op is how the brief obtains the
    # line — its stdout is what gets lifted. Before the op existed, `unchanged` printed
    # "unknown op" to stderr and returned 2.
    import subprocess, tempfile
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brief_render.py")
    sp = os.path.join(tempfile.mkdtemp(), "standup.json")
    standup = {"unchanged": [{"id": "H1"}, {"id": "H2"}, {"id": "H3"}]}
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(standup, f)
    proc = subprocess.run([sys.executable, tool, "unchanged", sp],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    # the op's stdout is lifted verbatim -> it MUST equal the function's own output
    assert proc.stdout.strip() == R.render_unchanged_line(standup)
    assert proc.stdout.strip() == "· 3 unchanged · walk them"


# --- FIX 3 (A8/A5): the census `unparsed` count is surfaced in the standup panel ---

def test_render_factory_standup_surfaces_unparsed_census():
    # A8/A5: the collector computed `unparsed` (## Watching-class bullets invisible to the queue)
    # but NOTHING rendered it (write-only). It must surface in the panel — the natural human home.
    # Before the fix, an all-groups-empty standup with unparsed=60 rendered "nothing waiting".
    data = {"groups": {"veto": [], "needs_you": [], "handed_off": [], "stuck": []},
            "totals": {"veto": 0, "needs_you": 0, "handed_off": 0, "stuck": 0},
            "census": {"parsed": 5, "grouped": 3, "drainable": 2,
                       "unparsed": 60, "unparsed_titles": ["A80 tail: verify", "H68 residual"]}}
    out = R.render_factory_standup(data)
    assert "60 unparsed" in out
    assert "A80 tail" in out
    assert "nothing waiting" not in out.lower()   # the panel renders, it is not the empty state


# ─── A93 §2c — citation honesty ──────────────────────────────────────────────

def test_render_citation_live_fact_cites_the_run():
    assert R.render_citation({"queried_live": True}, "2026-07-17T11:02:00Z",
                             run_date="2026-07-17") == "queried live 2026-07-17"
    assert R.render_citation({"source": "live"}, "2026-07-17T11:02:00Z",
                             run_date="2026-07-17") == "queried live 2026-07-17"


def test_render_citation_cache_fact_cites_generated_utc():
    assert R.render_citation({}, "2026-07-17T11:02:00Z") == "as of 2026-07-17T11:02:00Z"
    # a non-live item can NEVER claim "queried live"
    assert "queried live" not in R.render_citation({"queried_live": False}, "2026-07-17T11:02:00Z")


# ─── A93 §2a — auto-cleared render ───────────────────────────────────────────

def test_render_auto_cleared_reports_each_completed_deferral():
    cleared = [{"item_id": "OI-9", "title": "Wire the transfer"},
               {"item_id": "OI-8", "title": "File the note"}]
    out = R.render_auto_cleared(cleared)
    assert out == ("✅ auto-cleared: Wire the transfer (completed since deferral)\n"
                   "✅ auto-cleared: File the note (completed since deferral)")


def test_render_auto_cleared_reads_a_ledger_dict_and_empty_is_silent():
    assert R.render_auto_cleared({"auto_cleared_deferrals": []}) == ""
    assert R.render_auto_cleared([]) == ""
    assert R.render_auto_cleared({"auto_cleared_deferrals":
                                  [{"title": "X"}]}) == "✅ auto-cleared: X (completed since deferral)"


# ─── A93 §3 — the movement line ──────────────────────────────────────────────

def test_render_movement_shows_cleared_and_now_in_act():
    prev = {"stations": {"dev": [{"id": "A", "title": "Alpha"}]},
            "act": [{"id": "A", "title": "Alpha"}, {"id": "B", "title": "Beta"}]}
    # Alpha is gone (done); Beta stays; Gamma is newly in Act
    fresh = {"stations": {"dev": [{"id": "B", "title": "Beta"}]},
             "act": [{"id": "B", "title": "Beta"}, {"id": "C", "title": "Gamma"}]}
    out = R.render_movement(prev, fresh)
    assert "✅ 1 cleared since last brief — Alpha" in out
    assert "↑ now in Act: Gamma" in out
    assert "Beta" not in out.split("now in Act")[0]  # Beta was already in Act, not "now"


def test_render_movement_zero_delta_is_silent():
    same = {"stations": {"dev": [{"id": "A", "title": "Alpha"}]},
            "act": [{"id": "A", "title": "Alpha"}]}
    assert R.render_movement(same, same) == ""
    # no prior cache at all -> nothing cleared, everything "now in act" is NOT reported as cleared
    assert R.render_movement(None, same) == "↑ now in Act: Alpha"


def test_render_movement_collapses_many_cleared():
    prev = {"act": [{"id": str(i), "title": "T%d" % i} for i in range(8)]}
    fresh = {"act": []}
    out = R.render_movement(prev, fresh, collapse=5)
    assert "✅ 8 cleared" in out and "[expand]" in out
    assert out.count(",") == 4  # exactly the first 5 titles shown


def test_compute_movement_court_filtered_act_only():
    # an item routed to In-motion (court others) is NOT an Act row, so it is not "now in Act"
    prev = {"act": []}
    fresh = {"act": [{"id": "W", "title": "Waiting", "in_motion": {"court": "others"}},
                     {"id": "N", "title": "New move"}]}
    mv = R.compute_movement(prev, fresh)
    assert mv["now_in_act"] == ["New move"]


# ─── A93 §4 — delta-gated health lines ───────────────────────────────────────

def test_filter_health_lines_suppresses_unchanged():
    lines = {"pipeline": "⚙️ Pipeline: 3 runs", "factory": "🔧 clear ✓"}
    fps = R.health_fingerprints(lines)
    # second run, same text -> nothing shows (steady state = silence)
    shown, new_fps = R.filter_health_lines(lines, fps)
    assert shown == {}
    assert new_fps == fps


def test_filter_health_lines_shows_changed_and_first_appearance():
    prev = R.health_fingerprints({"pipeline": "⚙️ Pipeline: 3 runs", "factory": "🔧 clear ✓"})
    lines = {"pipeline": "⚙️ Pipeline: 5 runs · ⚠ 2 anomalies",  # changed
             "factory": "🔧 clear ✓",                            # unchanged
             "economic": "⚠ 1 economic figure with no paper"}    # first appearance
    shown, _ = R.filter_health_lines(lines, prev)
    assert set(shown) == {"pipeline", "economic"}
    assert "factory" not in shown


def test_filter_health_lines_ignores_whitespace_only_changes():
    a = {"pipeline": "⚙️  Pipeline:   3 runs"}
    b = {"pipeline": "⚙️ Pipeline: 3 runs"}
    shown, _ = R.filter_health_lines(b, R.health_fingerprints(a))
    assert shown == {}  # only whitespace differs -> steady state
