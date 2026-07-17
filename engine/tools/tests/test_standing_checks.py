#!/usr/bin/env python3
"""standing_checks.py tests (A94) — the perpetual-invariant runner + its brief render line.

Pure pytest module (def test_*), so conftest collects it into `python -m pytest -q`.
Predicates use `exit 0` / `exit 1` / a missing binary — portable across cmd (Windows) and sh.
"""
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):  # rendered lines carry emoji; piped Windows stdout is cp1252
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HARNESS_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../engine/tools
sys.path.insert(0, HARNESS_TOOLS)
import standing_checks
import brief_render

NOW = "2026-07-17T12:00:00Z"

# One of every status the runner must produce (the A94 acceptance matrix).
FIVE_CHECK_REGISTRY = """\
checks:
  - id: green-standing
    origin: "A1 / demo"
    kind: standing
    predicate: "exit 0"
    cadence: daily
    on_violation: "should never fire"
  - id: red-standing
    origin: "A2 / demo"
    kind: standing
    predicate: "exit 1"
    cadence: daily
    on_violation: "re-add the guard"
  - id: expired-watch
    origin: "A3 / a stale Watching line"
    kind: watch
    predicate: "exit 1"
    cadence: daily
    check_by: 2026-01-01
    on_violation: "escalate — never observed green"
  - id: green-watch
    origin: "A4 / a Watching line ready to clear"
    kind: watch
    predicate: "exit 0"
    cadence: daily
    check_by: 2026-12-31
    on_violation: "n/a"
    watching_line: "**A4 tails:** confirm the widget renders — delete once observed"
  - id: broken-binary
    origin: "A5 / demo"
    kind: standing
    predicate: "this_binary_does_not_exist_xyz --nope"
    cadence: daily
    on_violation: "install the missing tool"
"""


def _write(tmp_path, text, name="checks.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _by_id(result):
    return {c["id"]: c for c in result["checks"]}


def test_status_matrix(tmp_path):
    reg = _write(tmp_path, FIVE_CHECK_REGISTRY)
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    got = _by_id(result)
    assert got["green-standing"]["status"] == "green"
    assert got["red-standing"]["status"] == "red"
    assert got["expired-watch"]["status"] == "expired"
    assert got["green-watch"]["status"] == "observed"
    assert got["broken-binary"]["status"] == "red"
    # an unrunnable predicate is a red WITH a reason, never a silent skip (the A79 lesson)
    assert got["broken-binary"]["reason"]
    assert result["findings"] == []


def test_watch_green_lists_paired_watching_line(tmp_path):
    reg = _write(tmp_path, FIVE_CHECK_REGISTRY)
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    assert "**A4 tails:** confirm the widget renders — delete once observed" in result["watching_clear"]


def test_render_shows_exactly_red_and_expired(tmp_path):
    reg = _write(tmp_path, FIVE_CHECK_REGISTRY)
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    line = standing_checks.render(result)
    assert "⛑ standing-check red: red-standing — re-add the guard" in line
    assert "⛑ standing-check red: broken-binary" in line
    assert "👁 watch expired unobserved: expired-watch" in line
    # greens never render
    assert "green-standing" not in line
    assert "green-watch" not in line


def test_all_green_renders_nothing_and_delta_gate_is_silent(tmp_path):
    reg = _write(tmp_path, """\
checks:
  - id: a
    kind: standing
    predicate: "exit 0"
    cadence: daily
    on_violation: "x"
  - id: b
    kind: standing
    predicate: "exit 0"
    cadence: daily
    on_violation: "y"
""")
    r1 = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    line1 = standing_checks.render(r1)
    assert line1 == ""
    # feed through the shared A93 health-gate: first appearance of an empty line shows nothing,
    # and a repeat all-green run (identical text) is delta-suppressed.
    shown1, fps1 = brief_render.filter_health_lines({"standing": line1}, {})
    assert shown1.get("standing", "") == ""
    r2 = standing_checks.run(reg, cwd=str(tmp_path), now="2026-07-18T12:00:00Z")
    line2 = standing_checks.render(r2)
    shown2, _ = brief_render.filter_health_lines({"standing": line2}, fps1)
    assert "standing" not in shown2  # steady-state = silence


def test_red_line_delta_gated_shows_then_suppresses(tmp_path):
    reg = _write(tmp_path, """\
checks:
  - id: r
    kind: standing
    predicate: "exit 1"
    cadence: daily
    on_violation: "fix r"
""")
    r1 = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    line1 = standing_checks.render(r1)
    shown1, fps1 = brief_render.filter_health_lines({"standing": line1}, {})
    assert shown1["standing"].startswith("⛑ standing-check red: r")
    # same red next run → delta-suppressed (no re-nag)
    r2 = standing_checks.run(reg, cwd=str(tmp_path), now="2026-07-18T12:00:00Z", prior=r1)
    shown2, _ = brief_render.filter_health_lines({"standing": standing_checks.render(r2)}, fps1)
    assert "standing" not in shown2


def test_corrupt_registry_is_loud_finding_not_crash(tmp_path):
    reg = _write(tmp_path, "checks:\n    this is not a valid list item !!!\n")
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    assert result["checks"] == []
    assert result["findings"], "a corrupt registry must produce a loud finding"
    # the finding also surfaces as a loud render line, never a silent pass
    assert "registry" in standing_checks.render(result).lower()


def test_missing_registry_degrades_silent(tmp_path):
    result = standing_checks.run(str(tmp_path / "nope.yaml"), cwd=str(tmp_path), now=NOW)
    assert result["checks"] == []
    assert result["findings"] == []
    assert standing_checks.render(result) == ""


def test_check_missing_required_field_is_a_finding(tmp_path):
    reg = _write(tmp_path, """\
checks:
  - id: ok
    kind: standing
    predicate: "exit 0"
    cadence: daily
    on_violation: "x"
  - origin: "no id, no predicate"
    kind: standing
""")
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    assert _by_id(result)["ok"]["status"] == "green"
    assert result["findings"], "a record missing id/predicate must be a finding, not silently dropped"


def test_weekly_cadence_skips_when_not_due(tmp_path):
    reg = _write(tmp_path, """\
checks:
  - id: w
    kind: standing
    predicate: "exit 1"
    cadence: weekly
    on_violation: "fix w"
""")
    # first run: due (no prior) → executes → red
    r1 = standing_checks.run(reg, cwd=str(tmp_path), now="2026-07-17T12:00:00Z")
    assert _by_id(r1)["w"]["status"] == "red"
    # two days later: NOT due → carries the prior red forward without re-executing
    r2 = standing_checks.run(reg, cwd=str(tmp_path), now="2026-07-19T12:00:00Z", prior=r1)
    assert _by_id(r2)["w"]["status"] == "red"
    assert _by_id(r2)["w"]["last_run"] == _by_id(r1)["w"]["last_run"]  # not re-run
    # eight days later: due again
    r3 = standing_checks.run(reg, cwd=str(tmp_path), now="2026-07-25T12:00:00Z", prior=r1)
    assert _by_id(r3)["w"]["last_run"] != _by_id(r1)["w"]["last_run"]


def test_run_cli_writes_results_and_exits_zero(tmp_path):
    reg = _write(tmp_path, FIVE_CHECK_REGISTRY)
    out = str(tmp_path / "results.json")
    rc = standing_checks.main(["run", "--registry", reg, "--out", out,
                               "--cwd", str(tmp_path), "--now", NOW])
    assert rc == 0
    with open(out, encoding="utf-8") as f:
        result = json.load(f)
    assert _by_id(result)["red-standing"]["status"] == "red"


def test_run_cli_exits_zero_on_corrupt_registry(tmp_path):
    reg = _write(tmp_path, "checks:\n    garbage !!!\n")
    out = str(tmp_path / "results.json")
    rc = standing_checks.main(["run", "--registry", reg, "--out", out,
                               "--cwd", str(tmp_path), "--now", NOW])
    assert rc == 0  # collector contract: exit 0 always, errors become findings
    with open(out, encoding="utf-8") as f:
        assert json.load(f)["findings"]


def test_render_cli_reads_results(tmp_path):
    reg = _write(tmp_path, FIVE_CHECK_REGISTRY)
    out = str(tmp_path / "results.json")
    standing_checks.main(["run", "--registry", reg, "--out", out,
                          "--cwd", str(tmp_path), "--now", NOW])
    rc = standing_checks.main(["render", "--results", out])
    assert rc == 0


# ── folded review LOWs + the load-bearing coverage the review flagged ──

def test_scalar_keeps_multi_token_quoted_predicate_intact(tmp_path):
    # a shell predicate that both starts and ends with `"` but is NOT one token must NOT be
    # end-stripped (that would corrupt it into an unbalanced command) — the review foot-gun.
    reg = _write(tmp_path, '''\
checks:
  - id: q
    kind: standing
    predicate: "test -f a" && test -f "b"
    cadence: daily
    on_violation: "x"
''')
    checks = standing_checks.parse_registry((tmp_path / "checks.yaml").read_text(encoding="utf-8"))
    assert checks[0]["predicate"] == '"test -f a" && test -f "b"'
    # while a normal single-quoted-token value IS unwrapped
    reg2 = _write(tmp_path, 'checks:\n  - id: n\n    kind: standing\n    predicate: "exit 0"\n    on_violation: "y"\n', name="c2.yaml")
    assert standing_checks.parse_registry((tmp_path / "c2.yaml").read_text(encoding="utf-8"))[0]["predicate"] == "exit 0"


def test_quoted_value_with_colon_survives(tmp_path):
    reg = _write(tmp_path, '''\
checks:
  - id: u
    kind: standing
    predicate: "echo http://example.com"
    cadence: daily
    on_violation: "x"
''')
    checks = standing_checks.parse_registry((tmp_path / "checks.yaml").read_text(encoding="utf-8"))
    assert checks[0]["predicate"] == "echo http://example.com"


def test_comment_stripping_and_scalar_types(tmp_path):
    reg = _write(tmp_path, '''\
# a leading comment
checks:
  - id: c              # trailing comment dropped
    kind: watch
    predicate: "exit 0"
    cadence: weekly
    check_by: null
    on_violation: "keep # this hash (no leading space upstream) — but here it is data"
''')
    checks = standing_checks.parse_registry((tmp_path / "checks.yaml").read_text(encoding="utf-8"))
    assert checks[0]["id"] == "c"
    assert checks[0]["check_by"] is None
    assert checks[0]["cadence"] == "weekly"


def test_malformed_check_by_is_a_loud_finding(tmp_path):
    reg = _write(tmp_path, '''\
checks:
  - id: w
    kind: watch
    predicate: "exit 1"
    cadence: daily
    check_by: not-a-date
    on_violation: "escalate"
''')
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    assert any("unparseable check_by" in f for f in result["findings"])


def test_watch_red_with_future_check_by_is_watching_not_expired(tmp_path):
    reg = _write(tmp_path, '''\
checks:
  - id: w
    kind: watch
    predicate: "exit 1"
    cadence: daily
    check_by: 2099-01-01
    on_violation: "escalate"
''')
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW)
    assert _by_id(result)["w"]["status"] == "watching"
    assert standing_checks.render(result) == ""  # within its window → renders nothing


def test_first_red_pins_on_first_red_then_clears_on_green(tmp_path):
    red = _write(tmp_path, 'checks:\n  - id: f\n    kind: standing\n    predicate: "exit 1"\n    cadence: daily\n    on_violation: "x"\n')
    r1 = standing_checks.run(red, cwd=str(tmp_path), now="2026-07-17T00:00:00Z")
    first = _by_id(r1)["f"]["first_red"]
    assert first == "2026-07-17T00:00:00+00:00"
    # a persisting red keeps the ORIGINAL first_red while last_run advances (dates the breakage)
    r2 = standing_checks.run(red, cwd=str(tmp_path), now="2026-07-18T00:00:00Z", prior=r1)
    assert _by_id(r2)["f"]["first_red"] == first
    assert _by_id(r2)["f"]["last_run"] == "2026-07-18T00:00:00+00:00"
    # flipping to green clears first_red
    green = _write(tmp_path, 'checks:\n  - id: f\n    kind: standing\n    predicate: "exit 0"\n    cadence: daily\n    on_violation: "x"\n', name="g.yaml")
    r3 = standing_checks.run(green, cwd=str(tmp_path), now="2026-07-19T00:00:00Z", prior=r2)
    assert _by_id(r3)["f"]["first_red"] is None
    assert _by_id(r3)["f"]["status"] == "green"


def test_predicate_timeout_is_a_red_with_reason(tmp_path):
    # a quote-free predicate (a sleeper script run from cwd) — portable across cmd and sh
    (tmp_path / "sleeper.py").write_text("import time; time.sleep(5)\n", encoding="utf-8")
    reg = _write(tmp_path, 'checks:\n  - id: slow\n    kind: standing\n    predicate: python sleeper.py\n    cadence: daily\n    on_violation: "x"\n')
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW, timeout=1)
    entry = _by_id(result)["slow"]
    assert entry["status"] == "red"
    assert "timed out" in entry["reason"]


def test_malformed_prior_does_not_crash(tmp_path):
    reg = _write(tmp_path, 'checks:\n  - id: ok\n    kind: standing\n    predicate: "exit 0"\n    cadence: daily\n    on_violation: "x"\n')
    # a valid-JSON-but-wrong-shape prior (a non-dict check entry) must not break the exit-0 contract
    bad_prior = {"checks": ["not-a-dict", {"id": "ok", "status": "green"}]}
    result = standing_checks.run(reg, cwd=str(tmp_path), now=NOW, prior=bad_prior)
    assert _by_id(result)["ok"]["status"] == "green"
