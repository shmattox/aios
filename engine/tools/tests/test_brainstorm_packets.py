#!/usr/bin/env python3
"""brainstorm_packets.py tests (A77) — brief decision cards from brainstorm packets.

Pure pytest module (def test_*), so conftest collects it into `python -m pytest -q`.
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
import brainstorm_packets as bp

NOW = "2026-07-17T12:00:00Z"

# A conforming packet matching the GM19 contract exactly (spec §"The packet contract").
GOOD_PACKET = """\
---
type: brainstorm-packet
item: H87
status: awaiting-answers
questions:
  - id: q1
    header: Authorship
    question: "Who authors a registry entry when work closes?"
    options:
      - label: "Manual at close"
        description: "A human adds an entry only when the invariant matters."
      - label: "Gate auto-generates"
        description: "Every ship emits one automatically."
    default: "Manual at close"
  - id: q2
    header: Cadence
    question: "How often does the runner fire?"
    options:
      - label: "Nightly"
        description: "Rides the existing brief-cache gather."
      - label: "Weekly"
        description: "A slower sweep."
    default: "Nightly"
answers: {}
---

# H87 brainstorm packet

## Context
Prose the later spec-authoring session reads. Not rendered as a card.
"""


def _write(tmp_path, text, name="packet.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ── parsing ──

def test_parse_packet_full_structure():
    data = bp.parse_packet(GOOD_PACKET)
    assert data["type"] == "brainstorm-packet"
    assert data["item"] == "H87"
    assert data["status"] == "awaiting-answers"
    assert data["answers"] == {}
    assert [q["id"] for q in data["questions"]] == ["q1", "q2"]
    q1 = data["questions"][0]
    assert q1["header"] == "Authorship"
    assert q1["question"] == "Who authors a registry entry when work closes?"
    assert q1["default"] == "Manual at close"
    assert [o["label"] for o in q1["options"]] == ["Manual at close", "Gate auto-generates"]
    assert q1["options"][0]["description"].startswith("A human adds")


# ── scan / cards ──

def test_scan_valid_packet_yields_verbatim_card(tmp_path):
    _write(tmp_path, GOOD_PACKET)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["findings"] == []
    assert len(result["cards"]) == 1
    card = result["cards"][0]
    assert card["item"] == "H87"
    assert [q["id"] for q in card["questions"]] == ["q1", "q2"]
    # options + default are lifted verbatim (deterministic-render rule)
    assert card["questions"][0]["default"] == "Manual at close"
    assert [o["label"] for o in card["questions"][0]["options"]] == \
        ["Manual at close", "Gate auto-generates"]


def test_scan_skips_answered_and_committed_and_non_packets(tmp_path):
    _write(tmp_path, GOOD_PACKET.replace("status: awaiting-answers", "status: answered"),
           name="answered.md")
    _write(tmp_path, GOOD_PACKET.replace("status: awaiting-answers", "status: spec-committed"),
           name="committed.md")
    _write(tmp_path, "---\ntype: finding\nstatus: awaiting-answers\n---\nnot a packet\n",
           name="finding.md")
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert result["findings"] == []


def test_scan_zero_pending_renders_nothing(tmp_path):
    # empty dir + a missing dir both degrade to a clean, silent result
    result = bp.scan([str(tmp_path), str(tmp_path / "does-not-exist")], now=NOW)
    assert result["cards"] == []
    assert result["findings"] == []
    assert bp.render(result) == ""


def test_scan_fully_answered_awaiting_packet_renders_no_card(tmp_path):
    # every question already in answers (but status not yet flipped) → no pending questions → no card
    both = GOOD_PACKET.replace("answers: {}",
                               'answers:\n  q1: "Manual at close"\n  q2: "Nightly"')
    _write(tmp_path, both)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert result["findings"] == []


def test_scan_partially_answered_renders_only_pending(tmp_path):
    partial = GOOD_PACKET.replace("answers: {}", 'answers:\n  q1: "Manual at close"')
    _write(tmp_path, partial)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert len(result["cards"]) == 1
    assert [q["id"] for q in result["cards"][0]["questions"]] == ["q2"]


# ── contract refusal (malformed → loud finding, never a card) ──

def test_missing_default_is_refused_with_loud_finding(tmp_path):
    bad = GOOD_PACKET.replace('    default: "Manual at close"\n', "", 1)
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert len(result["findings"]) == 1
    assert "default" in result["findings"][0]
    # the finding surfaces as one loud render line
    line = bp.render(result)
    assert line.startswith("⚠ brainstorm packet malformed:")
    assert "packet.md" in line


def test_default_naming_no_option_is_refused(tmp_path):
    bad = GOOD_PACKET.replace('    default: "Manual at close"', '    default: "Nonexistent"', 1)
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert any("names no option label" in f for f in result["findings"])


def test_empty_questions_is_refused(tmp_path):
    bad = """\
---
type: brainstorm-packet
item: X
status: awaiting-answers
questions: []
answers: {}
---
body
"""
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert any("no questions" in f for f in result["findings"])


def test_option_without_label_is_refused(tmp_path):
    bad = GOOD_PACKET.replace('      - label: "Gate auto-generates"\n'
                              '        description: "Every ship emits one automatically."\n',
                              '      - description: "no label here"\n', 1)
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert any("no label" in f for f in result["findings"])


def test_duplicate_question_id_is_refused(tmp_path):
    bad = GOOD_PACKET.replace("  - id: q2", "  - id: q1", 1)
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert any("duplicate question id" in f for f in result["findings"])


def test_torn_frontmatter_declared_packet_is_a_finding(tmp_path):
    # a file whose top-level scalars say it's an awaiting packet but the fence never closes
    torn = "---\ntype: brainstorm-packet\nstatus: awaiting-answers\nquestions:\n  - id: q1\n"
    # read_frontmatter needs a closing fence to read type/status; without one it's not seen as a
    # packet at all (can't be classified) — so it is silently skipped, NOT a false card.
    _write(tmp_path, torn)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert result["findings"] == []


def test_declared_packet_with_garbage_questions_block_is_a_finding(tmp_path):
    # fence closes (so it IS classified as an awaiting packet) but the questions block is unparseable
    bad = "---\ntype: brainstorm-packet\nitem: X\nstatus: awaiting-answers\n" \
          "questions:\n      - id: q1\n  bad-dedent-line\n---\nbody\n"
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert len(result["findings"]) == 1


# ── render (delta-gate integration) ──

def test_render_findings_delta_gated(tmp_path):
    import brief_render
    bad = GOOD_PACKET.replace('    default: "Manual at close"\n', "", 1)
    _write(tmp_path, bad)
    r1 = bp.scan([str(tmp_path)], now=NOW)
    line1 = bp.render(r1)
    shown1, fps1 = brief_render.filter_health_lines({"packets": line1}, {})
    assert shown1["packets"].startswith("⚠ brainstorm packet malformed:")
    # same finding next run → delta-suppressed (no re-nag)
    r2 = bp.scan([str(tmp_path)], now="2026-07-18T12:00:00Z")
    shown2, _ = brief_render.filter_health_lines({"packets": bp.render(r2)}, fps1)
    assert "packets" not in shown2


# ── answer write-back round-trip ──

def test_answer_roundtrip_writes_status_and_answers(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    status, _ = bp.answer(p, {"q1": "Manual at close", "q2": "Nightly"})
    assert status == "answered"
    text = open(p, encoding="utf-8").read()
    data = bp.parse_packet(text)
    assert data["status"] == "answered"
    assert data["answers"] == {"q1": "Manual at close", "q2": "Nightly"}
    # the packet is no longer a pending card
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []


def test_answer_preserves_questions_block_and_prose(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    bp.answer(p, {"q1": "Gate auto-generates", "q2": "Weekly"})
    text = open(p, encoding="utf-8").read()
    # the questions block + prose are byte-preserved; only status + answers changed
    assert "## Context" in text
    assert "Prose the later spec-authoring session reads" in text
    assert 'question: "Who authors a registry entry when work closes?"' in text
    assert "header: Authorship" in text
    # re-parse yields the full original question set intact
    data = bp.parse_packet(text)
    assert [q["id"] for q in data["questions"]] == ["q1", "q2"]
    assert data["questions"][0]["default"] == "Manual at close"


def test_answer_partial_keeps_awaiting_status(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    status, _ = bp.answer(p, {"q1": "Manual at close"})
    assert status == "awaiting-answers"
    data = bp.parse_packet(open(p, encoding="utf-8").read())
    assert data["status"] == "awaiting-answers"
    assert data["answers"] == {"q1": "Manual at close"}


def test_answer_unknown_question_id_fails_loud(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    try:
        bp.answer(p, {"q9": "whatever"})
        assert False, "expected ValueError for an unknown question id"
    except ValueError as e:
        assert "unknown question id" in str(e)


def test_answer_free_text_other_is_accepted(tmp_path):
    # AskUserQuestion "Other" produces a value that is not one of the labels — allowed
    p = _write(tmp_path, GOOD_PACKET)
    status, _ = bp.answer(p, {"q1": "A third option I typed", "q2": "Nightly"})
    assert status == "answered"
    data = bp.parse_packet(open(p, encoding="utf-8").read())
    assert data["answers"]["q1"] == "A third option I typed"


def test_answer_value_with_quotes_survives_roundtrip(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    bp.answer(p, {"q1": 'has "quotes" inside', "q2": "Nightly"})
    data = bp.parse_packet(open(p, encoding="utf-8").read())
    assert data["answers"]["q1"] == 'has "quotes" inside'


# ── review-gate fix pass (BP-1 exit-0 contract, BP-2 newline write-path, BP-3 missing prompt) ──

def test_scan_survives_non_utf8_sibling_file(tmp_path):
    # BP-1: a mis-encoded .md in the packet dir must NOT abort the scan (exit-0 collector contract).
    _write(tmp_path, GOOD_PACKET)
    (tmp_path / "latin1.md").write_bytes("caf\xe9 - not utf-8\n".encode("latin-1"))
    result = bp.scan([str(tmp_path)], now=NOW)  # must not raise
    assert len(result["cards"]) == 1  # the good packet still surfaces


def test_scan_cli_exits_zero_with_non_utf8_sibling(tmp_path):
    _write(tmp_path, GOOD_PACKET)
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe not valid utf-8 \x80\x81\n")
    out = str(tmp_path / "cards.json")
    rc = bp.main(["scan", "--dirs", str(tmp_path), "--out", out, "--now", NOW])
    assert rc == 0
    with open(out, encoding="utf-8") as f:
        assert len(json.load(f)["cards"]) == 1


def test_deeply_nested_packet_is_a_finding_not_a_crash(tmp_path):
    # BP-1 same-root-cause: a pathologically nested block becomes a loud finding, never RecursionError.
    nested = "a:\n" + "".join(" " * (2 * d) + "k%d:\n" % d for d in range(1, 40))
    bad = "---\ntype: brainstorm-packet\nitem: X\nstatus: awaiting-answers\n" \
          "questions:\n  - id: q1\n    " + nested.replace("\n", "\n    ") + "\n---\nbody\n"
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)  # must not raise RecursionError
    assert result["cards"] == []
    assert len(result["findings"]) == 1


def test_answer_with_newline_value_roundtrips(tmp_path):
    # BP-2: a newline-bearing answer (an "Other" free-text value) must round-trip, not corrupt.
    p = _write(tmp_path, GOOD_PACKET)
    status, _ = bp.answer(p, {"q1": "line one\nline two", "q2": "Nightly"})
    assert status == "answered"
    data = bp.parse_packet(open(p, encoding="utf-8").read())
    assert data["answers"]["q1"] == "line one\nline two"
    assert data["answers"]["q2"] == "Nightly"
    # the packet still parses fully (questions block intact)
    assert [q["id"] for q in data["questions"]] == ["q1", "q2"]


def test_answer_with_tab_and_cr_value_roundtrips(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    bp.answer(p, {"q1": "a\tb\rc", "q2": "Nightly"})
    data = bp.parse_packet(open(p, encoding="utf-8").read())
    assert data["answers"]["q1"] == "a\tb\rc"


def test_missing_question_text_is_refused(tmp_path):
    # BP-3: a question with options + default but no `question:` prompt is not a renderable card.
    bad = GOOD_PACKET.replace(
        '    question: "Who authors a registry entry when work closes?"\n', "", 1)
    _write(tmp_path, bad)
    result = bp.scan([str(tmp_path)], now=NOW)
    assert result["cards"] == []
    assert any("no question text" in f for f in result["findings"])


# ── CLI (collector contract: scan/render exit 0 always; answer fails loud) ──

def test_scan_cli_exits_zero_and_writes_sidecar(tmp_path):
    _write(tmp_path, GOOD_PACKET)
    out = str(tmp_path / "cards.json")
    rc = bp.main(["scan", "--dirs", str(tmp_path), "--out", out, "--now", NOW])
    assert rc == 0
    with open(out, encoding="utf-8") as f:
        result = json.load(f)
    assert len(result["cards"]) == 1


def test_scan_cli_exits_zero_on_malformed(tmp_path):
    _write(tmp_path, GOOD_PACKET.replace('    default: "Manual at close"\n', "", 1))
    out = str(tmp_path / "cards.json")
    rc = bp.main(["scan", "--dirs", str(tmp_path), "--out", out, "--now", NOW])
    assert rc == 0  # collector contract
    with open(out, encoding="utf-8") as f:
        assert json.load(f)["findings"]


def test_render_cli_reads_sidecar(tmp_path):
    _write(tmp_path, GOOD_PACKET)
    out = str(tmp_path / "cards.json")
    bp.main(["scan", "--dirs", str(tmp_path), "--out", out, "--now", NOW])
    assert bp.main(["render", "--results", out]) == 0


def test_answer_cli_roundtrips(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    rc = bp.main(["answer", "--packet", p, "--answers",
                  json.dumps({"q1": "Manual at close", "q2": "Nightly"})])
    assert rc == 0
    assert bp.parse_packet(open(p, encoding="utf-8").read())["status"] == "answered"


def test_answer_cli_bad_id_exits_nonzero(tmp_path):
    p = _write(tmp_path, GOOD_PACKET)
    rc = bp.main(["answer", "--packet", p, "--answers", json.dumps({"nope": "x"})])
    assert rc == 1
