#!/usr/bin/env python3
"""brainstorm_packets.py — brief decision cards from brainstorm packets (A77, engine leg of GM19).

The brainstorm step is otherwise fully synchronous: every ◷ seed waits for a sit-down Socratic
session. The GM19 `factory-packet` skill pre-runs every solo-runnable leg and freezes the residual
Seth-judgment into a machine-readable `questions:` block. This tool is the aios render leg: the
brief gather finds packets with `status: awaiting-answers`, validates each against the contract,
and produces the pending question set as decision-card DATA that the brief walk renders through the
standard AskUserQuestion affordance. On answer it writes `answers` + `status: answered` back into
the packet file (act-then-tell — a tactical write in the repo the packet lives in).

Two surfaces, one boundary (the deterministic-render rule, GM19 spec §"The packet contract"):
  - a WELL-FORMED awaiting-answers packet → a card whose `questions[]` are lifted VERBATIM; the
    model authors nothing, it only presents the frozen data.
  - a MALFORMED packet → refused with one loud health `finding`, NEVER rendered as a card (a
    half-parsed question set must not reach the human as a decision).

Contract (the env-health-collect / standing_checks envelope): `scan` ALWAYS exits 0 — a missing
packet dir degrades silent, a packet declaring `type: brainstorm-packet` but violating the contract
becomes a loud finding, and a valid packet with no pending questions renders nothing. `scan` and
`render` are read-only; only `answer` writes, and only the one packet named on its command line.

Fact-free: packet directories are an argument (`--dirs`) — the gather resolves them from the
profile, the engine never hardcodes a path. stdlib only (no pyyaml — the frontmatter.py precedent);
the packet's nested `questions:`/`options:` block is parsed by the scoped mini-block reader below,
NOT the flat `read_frontmatter` (which only reads top-level scalars).

Usage:
  python brainstorm_packets.py scan --dirs <dir>[,<dir>...] --out <cards.json> [--now ISO]
  python brainstorm_packets.py render --results <cards.json>
  python brainstorm_packets.py answer --packet <packet.md> --answers '{"q1":"Label",...}'
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

from frontmatter import read_frontmatter

PACKET_TYPE = "brainstorm-packet"
AWAITING = "awaiting-answers"
ANSWERED = "answered"


# ─────────────────────────── time ───────────────────────────

def _now_iso(now=None):
    if now:
        try:
            dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── frontmatter split ───────────────────────────

def split_frontmatter(text):
    """`text` -> (fm_lines, body_lines). fm_lines are the lines BETWEEN the two `---` fences;
    body_lines start AT the closing fence (so a rejoin is lossless). Raises ValueError when there
    is no leading `---` or no closing fence — the caller turns that into a finding."""
    if not text.startswith("---"):
        raise ValueError("no leading '---' frontmatter fence")
    lines = text.split("\n")
    close = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            close = idx
            break
    if close is None:
        raise ValueError("unterminated frontmatter (no closing '---')")
    return lines[1:close], lines[close:]


# ─────────────────────────── mini block-YAML parser ───────────────────────────
# A tightly-scoped indentation parser for the packet frontmatter's ONE shape: top-level scalars +
# a `questions:` block sequence of maps (each carrying an `options:` block sequence) + an `answers:`
# map. NOT a general YAML parser — the engine is stdlib-only by discipline (frontmatter.py /
# standing_checks.py set the precedent). Anything outside the shape raises ValueError -> a finding.

def _strip_comment(line):
    """Drop a trailing `#…` comment not inside quotes (a `#` mid-token or quoted is data)."""
    out, q, i = [], None, 0
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if c == q:
                q = None
        elif c in ("'", '"'):
            q = c
            out.append(c)
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).rstrip()


def _read_quoted(v):
    """Read a leading quoted scalar from `v`. Returns (unescaped_value, chars_consumed). Double
    quotes honor `\\"`/`\\\\`/`\\n`/`\\t` escapes (so an answer containing a quote round-trips);
    single quotes honor the YAML `''` escape. An unterminated quote consumes the whole string."""
    q = v[0]
    out, i = [], 1
    while i < len(v):
        c = v[i]
        if q == '"' and c == "\\" and i + 1 < len(v):
            out.append({'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}.get(v[i + 1], v[i + 1]))
            i += 2
            continue
        if q == "'" and c == "'" and i + 1 < len(v) and v[i + 1] == "'":
            out.append("'")
            i += 2
            continue
        if c == q:
            return "".join(out), i + 1
        out.append(c)
        i += 1
    return "".join(out), i


def _scalar(v):
    """A YAML scalar value token -> Python. A single balanced quoted token is unwrapped (and, for
    double quotes, unescaped); a value that merely starts with a quote but is NOT one token (a shell
    predicate like `"a" && "b"`) is left raw; null/~ -> None; true/false -> bool; empty -> None."""
    v = v.strip()
    if v == "":
        return None
    if v[0] in ("'", '"'):
        s, consumed = _read_quoted(v)
        if consumed == len(v):  # the quoted token spans the whole value
            return s
    low = v.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return v


def _tokenize(fm_lines):
    """[(indent, content)] for every non-blank, comment-stripped frontmatter line."""
    toks = []
    for raw in fm_lines:
        s = _strip_comment(raw)
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip(" "))
        toks.append((indent, s.strip()))
    return toks


_MAX_DEPTH = 12  # the contract shape nests ~5 deep; a deeper block is malformed, not a stack crash


def _parse_block(toks, i, indent, depth=0):
    """Parse the map or sequence whose members sit at `indent`, starting at toks[i]. Returns
    (value, next_i). A block whose first member starts with `- ` is a sequence; else a mapping.
    `depth` caps recursion so a pathologically nested file becomes a ValueError finding, never a
    RecursionError abort of the collector run."""
    if depth > _MAX_DEPTH:
        raise ValueError("frontmatter nested too deep (>%d)" % _MAX_DEPTH)
    if i >= len(toks):
        return None, i
    if toks[i][1] == "-" or toks[i][1].startswith("- "):
        return _parse_seq(toks, i, indent, depth)
    return _parse_map(toks, i, indent, depth)


def _parse_map(toks, i, indent, depth=0):
    result = {}
    while i < len(toks):
        ind, content = toks[i]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError("unexpected indent at %r" % content)
        if content == "-" or content.startswith("- "):
            raise ValueError("list item where a mapping key was expected: %r" % content)
        if ":" not in content:
            raise ValueError("expected 'key: value', got %r" % content)
        key, _, val = content.partition(":")
        key = key.strip()
        val = val.strip()
        i += 1
        if val == "":
            if i < len(toks) and toks[i][0] > indent:
                child, i = _parse_block(toks, i, toks[i][0], depth + 1)
                result[key] = child
            else:
                result[key] = None
        elif val == "{}":
            result[key] = {}
        elif val == "[]":
            result[key] = []
        else:
            result[key] = _scalar(val)
    return result, i


def _parse_seq(toks, i, indent, depth=0):
    items = []
    while i < len(toks):
        ind, content = toks[i]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError("unexpected indent in sequence at %r" % content)
        if not (content == "-" or content.startswith("- ")):
            break
        inline = content[1:].strip()  # text after the dash, e.g. "id: q1" (or "" for a bare dash)
        i += 1
        # The item's map: the inline `key: value` (synthesized at indent+2, where it visually sits)
        # plus every following line more-indented than the dash.
        item_toks = []
        if inline:
            item_toks.append((indent + 2, inline))
        while i < len(toks) and toks[i][0] > indent:
            item_toks.append(toks[i])
            i += 1
        if not item_toks:
            raise ValueError("empty sequence item under indent %d" % indent)
        base = item_toks[0][0]
        item, _ = _parse_map(item_toks, 0, base, depth + 1)
        items.append(item)
    return items, i


def parse_packet(text):
    """A packet file's full text -> the frontmatter dict (top-level scalars + parsed `questions`
    and `answers`). Raises ValueError on any shape it cannot parse (torn frontmatter, a stray line)
    so the caller can turn it into a finding."""
    fm_lines, _ = split_frontmatter(text)
    toks = _tokenize(fm_lines)
    data, _ = _parse_map(toks, 0, 0)
    return data


# ─────────────────────────── contract validation ───────────────────────────

def validate_packet(data):
    """Apply the render-relevant subset of the GM19 `packet_check` contract: `questions` is a
    non-empty list; every question carries an `id`, a non-empty `options` list of `{label, ...}`,
    and a `default` that NAMES one of its option labels. Returns a reason string on the FIRST
    violation, or None when the packet is renderable. (The four ecosystem-leg anchors that
    `packet_check` also enforces live in the packet PROSE, not this frontmatter — they gate the
    later spec commit via speccheck, not the card render.)"""
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        return "no questions (contract requires a non-empty questions block)"
    seen_ids = set()
    for n, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            return "question %d is not a mapping" % n
        qid = q.get("id")
        if not qid:
            return "question %d missing id" % n
        if qid in seen_ids:
            return "duplicate question id %r" % qid
        seen_ids.add(qid)
        text = q.get("question")
        if not isinstance(text, str) or not text.strip():
            return "question %r has no question text" % qid  # a card with no prompt is not renderable
        opts = q.get("options")
        if not isinstance(opts, list) or not opts:
            return "question %r has no options" % qid
        labels = []
        for o in opts:
            if not isinstance(o, dict) or not o.get("label"):
                return "question %r has an option with no label" % qid
            labels.append(o["label"])
        default = q.get("default")
        if default in (None, ""):
            return "question %r has no default" % qid
        if default not in labels:
            return "question %r default %r names no option label" % (qid, default)
    answers = data.get("answers")
    if answers not in (None, {}) and not isinstance(answers, dict):
        return "answers is not a mapping"
    return None


# ─────────────────────────── scan ───────────────────────────

def _card_from_packet(path, data):
    """Build the card payload from a validated packet: item id + every PENDING question (one whose
    id is not already in `answers`), each with its options + default lifted verbatim."""
    answered = data.get("answers") or {}
    pending = []
    for q in data["questions"]:
        if q["id"] in answered:
            continue
        pending.append({
            "id": q["id"],
            "header": q.get("header"),
            "question": q.get("question"),
            "options": [{"label": o["label"], "description": o.get("description")}
                        for o in q["options"]],
            "default": q["default"],
        })
    return {
        "packet": os.path.abspath(path),
        "item": data.get("item"),
        "questions": pending,
    }


def scan(dirs, now=None):
    """Walk each packet directory, classify every `*.md`, and return the results dict. NEVER raises
    on a bad packet — a declared-but-malformed packet becomes a finding, a non-packet is skipped
    silently, a valid awaiting-answers packet with pending questions becomes a card. A missing dir
    degrades silent (collector contract)."""
    result = {"generated_utc": _now_iso(now), "cards": [], "findings": []}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue  # degrade silent — an absent/unconfigured dir means nothing to scan
        for path in sorted(glob.glob(os.path.join(d, "*.md"))):
            try:
                # errors="replace" so a stray non-UTF-8 byte in a findings-dir file NEVER aborts the
                # scan (the exit-0 collector contract): a genuine packet still surfaces its ASCII
                # frontmatter, and a non-packet is skipped either way. (answer() reads STRICT UTF-8 —
                # a write must refuse a file it cannot cleanly decode, not silently mangle it.)
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            flat = read_frontmatter(text)  # cheap top-level read: is this even a packet?
            if flat.get("type") != PACKET_TYPE:
                continue  # not a packet — skip silently
            if flat.get("status") != AWAITING:
                continue  # answered / spec-committed / other — not pending, renders nothing
            name = os.path.basename(path)
            try:
                data = parse_packet(text)
            except (ValueError, RecursionError) as e:
                # RecursionError guards a pathologically deep block; the depth cap in _parse_block
                # normally turns that into a ValueError first, but catch it too so a malformed packet
                # is ALWAYS a loud finding, never an abort of the whole gather.
                result["findings"].append("%s: %s" % (name, e))
                continue
            reason = validate_packet(data)
            if reason:
                result["findings"].append("%s: %s" % (name, reason))
                continue
            card = _card_from_packet(path, data)
            if card["questions"]:  # a fully-answered-but-still-awaiting packet renders nothing
                result["cards"].append(card)
    return result


# ─────────────────────────── render ───────────────────────────

def render(result):
    """The brainstorm-packet health line(s), lifted verbatim into the brief header then delta-gated
    by the shared A93 health-gate. Findings ONLY — a malformed packet is refused loudly; the valid
    pending packets surface as CARDS in the walk, not as a health line. Empty string when clean."""
    lines = ["⚠ brainstorm packet malformed: %s" % f for f in (result.get("findings") or [])]
    return "\n".join(lines)


# ─────────────────────────── answer write-back ───────────────────────────

def _esc(v):
    """Escape a scalar for a double-quoted frontmatter value. MUST stay the exact inverse of
    `_read_quoted`'s double-quote decode — a newline/tab left literal here would tear the value
    across lines when the packet is re-tokenized (the BP-2 write-path corruption). Backslash first,
    then the chars whose escapes _read_quoted recognizes."""
    return (str(v).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def _render_answers_block(answers):
    if not answers:
        return ["answers: {}"]
    out = ["answers:"]
    for k, v in answers.items():
        out.append('  %s: "%s"' % (k, _esc(v)))
    return out


def _is_top_key(line, key):
    """True iff `line` is the top-level (unindented) `key:` line."""
    if line[:1] in (" ", "\t") or ":" not in line:
        return False
    return line.split(":", 1)[0].strip() == key


def write_answers(text, answers, status):
    """Rewrite a packet's frontmatter: replace the top-level `status:` value with `status` and the
    top-level `answers:` block with `answers`. Every other line — the whole `questions:` block and
    the prose body — passes through untouched, so the change is surgical. Returns the new text."""
    fm_lines, body_lines = split_frontmatter(text)
    new_fm, i = [], 0
    handled_status = handled_answers = False
    while i < len(fm_lines):
        line = fm_lines[i]
        if _is_top_key(line, "status"):
            new_fm.append("status: %s" % status)
            handled_status = True
            i += 1
            continue
        if _is_top_key(line, "answers"):
            i += 1
            # consume the answers block: following blank/indented lines, stopping at the next
            # top-level key.
            while i < len(fm_lines):
                nxt = fm_lines[i]
                if nxt.strip() and nxt[:1] not in (" ", "\t"):
                    break
                i += 1
            new_fm.extend(_render_answers_block(answers))
            handled_answers = True
            continue
        new_fm.append(line)
        i += 1
    if not handled_status:
        new_fm.append("status: %s" % status)
    if not handled_answers:
        new_fm.extend(_render_answers_block(answers))
    return "\n".join(["---"] + new_fm + body_lines)


def answer(packet_path, answers):
    """Validate the packet, merge `answers` (a {q_id: label} map) into it, and write it back.
    Returns (new_status, message). Raises ValueError on an unreadable/malformed packet or an answer
    keyed to a question the packet does not contain (fail loud — this is a deliberate write, not a
    collector pass). `status` becomes `answered` only when every question is now answered."""
    with open(packet_path, encoding="utf-8") as f:
        text = f.read()
    data = parse_packet(text)
    reason = validate_packet(data)
    if reason:
        raise ValueError("refusing to answer a malformed packet: %s" % reason)
    qids = {q["id"] for q in data["questions"]}
    unknown = [k for k in answers if k not in qids]
    if unknown:
        raise ValueError("answer(s) for unknown question id(s): %s" % ", ".join(sorted(unknown)))
    merged = dict(data.get("answers") or {})
    merged.update(answers)
    status = ANSWERED if qids <= set(merged) else AWAITING
    new_text = write_answers(text, merged, status)
    # Self-verify BEFORE touching disk: the rewritten packet must still parse, still be a valid
    # packet, and read back the exact answers we wrote. A writer/reader asymmetry (BP-2's class)
    # thus fails loud on a temp string and never corrupts the user's file.
    try:
        check = parse_packet(new_text)
    except (ValueError, RecursionError) as e:
        raise ValueError("write would corrupt the packet frontmatter (%s) — refusing" % e)
    if validate_packet(check) or check.get("answers") != merged or check.get("status") != status:
        raise ValueError("write did not round-trip cleanly — refusing (answer value unrepresentable?)")
    tmp = packet_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, packet_path)
    return status, "wrote %d answer(s); status: %s" % (len(merged), status)


# ─────────────────────────── io ───────────────────────────

def _atomic_write(path, obj):
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ─────────────────────────── cli ───────────────────────────

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # rendered lines carry emoji; Windows console is cp1252
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="brainstorm_packets.py",
        description="Render brainstorm-packet questions as brief decision cards; capture answers.")
    sub = ap.add_subparsers(dest="op", required=True)

    s = sub.add_parser("scan", help="discover + validate packets, write the cards sidecar")
    s.add_argument("--dirs", required=True, help="comma-separated packet directories")
    s.add_argument("--out", required=True, help="cards.json sidecar path")
    s.add_argument("--now", help="ISO timestamp override (tests)")

    r = sub.add_parser("render", help="print the delta-gate-able health line from a cards sidecar")
    r.add_argument("--results", required=True, help="cards.json sidecar path")

    a = sub.add_parser("answer", help="write answers + status:answered into a packet file")
    a.add_argument("--packet", required=True, help="packet .md path")
    a.add_argument("--answers", required=True, help='JSON map {"q1":"Label",...}')

    args = ap.parse_args(argv)

    if args.op == "scan":
        dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
        result = scan(dirs, now=args.now)
        if not _atomic_write(args.out, result):
            print("WARN: could not write %s" % args.out, file=sys.stderr)
        print("brainstorm-packets: %d card(s), %d finding(s)"
              % (len(result["cards"]), len(result["findings"])), file=sys.stderr)
        return 0  # collector contract: exit 0 always

    if args.op == "render":
        line = render(_load(args.results))
        if line:
            print(line)
        return 0

    if args.op == "answer":
        try:
            answers = json.loads(args.answers)
            if not isinstance(answers, dict):
                raise ValueError("--answers must be a JSON object")
            status, msg = answer(args.packet, answers)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 1  # a deliberate write fails LOUD (unlike the collector ops)
        print(msg)
        return 0

    return 0  # unreachable (subparser required)


if __name__ == "__main__":
    sys.exit(main())
