#!/usr/bin/env python3
"""state_validate.py — one shared, fact-free state validator for the AIOS engine.

Validates state-engine notes (markdown frontmatter or .ndjson rows) against a schema passed
explicitly with --schema. Checks structural conformance only: type discriminator, required
keys, enum membership, bool/date shape, and wikilink-shaped relations. It does NOT verify
business/economic truth (that is Paper-Governs / human review at the gate).

Supersedes the near-verbatim copies at SecondBrain/02_FamilyOffice/state/validate_state.py and
Projects/general-management/state/validate_state.py: the logic is schema-driven, so the ONLY
per-engine difference is the schema.yaml — now supplied via --schema, so there is nothing
person/instance-specific baked in (Stage Contract: fact-free — zero hardcoded paths/ids).

stdlib ONLY (like every engine tool: capture.py, queue_tx.py, session_synth.py). The GM/FO
originals imported PyYAML; the engine is stdlib-only, so this module carries a small YAML-SUBSET
parser (_parse_yaml / _extract_frontmatter) covering exactly the two shapes the real files use:
flat `key: value` frontmatter, 2-level-nested schema maps, inline flow lists, and simple block
sequences (`key:` followed by `- item` lines). It does NOT support advanced YAML (anchors, block
scalars, deeper nesting).

  python state_validate.py --schema <schema.yaml> <note.md> [<note.md> ...]   # validate files
  python state_validate.py --schema <schema.yaml> --all [<tables_dir>]        # walk **/*.md + **/*.ndjson
"""
import json
import re
import sys
from pathlib import Path

_WIKILINK = re.compile(r"^\[\[[^\[\]]+\]\]$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─────────────────────────── stdlib YAML-subset parser ───────────────────────────
# Handles ONLY what the real schema.yaml + note frontmatter contain: flat and 2-level-nested
# `key: value` maps, inline flow lists `[a, b, c]` (quotes respected), full-line and inline
# `# comments`, quoted/`null`/`true`/`false` scalars. Anything richer is out of scope by design.

def _strip_comment(line):
    """Drop an inline `# comment`. Per YAML, `#` opens a comment ONLY at line start or when
    preceded by whitespace, and never inside quotes — so a URL like `.../a#frag` (no space
    before `#`) is preserved verbatim."""
    in_single = in_double = False
    for i, c in enumerate(line):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t":
                return line[:i]
    return line


def _is_quoted(s):
    """True iff s is wrapped in a single surrounding pair of matching quotes."""
    return len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'"


_DQ_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _decode_double(inner):
    """Decode the escape sequences a YAML DOUBLE-quoted scalar defines: \\n \\t \\r \\" \\\\.
    An unknown \\x sequence is left literal (backslash preserved). Strictly additive: a string
    with no backslash is returned unchanged."""
    if "\\" not in inner:
        return inner
    out, i, n = [], 0, len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n and inner[i + 1] in _DQ_ESCAPES:
            out.append(_DQ_ESCAPES[inner[i + 1]])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _unquote(s):
    """Strip a single surrounding pair of matching quotes AND decode the escapes that quote
    style defines — QUOTE-STYLE-AWARE and strictly ADDITIVE (any value that already parsed
    correctly is unchanged: no backslash / no `''` -> identical output):
      * single-quoted: `''` -> `'` (YAML's only single-quote escape; NO backslash processing)
      * double-quoted: `\\n \\t \\r \\" \\\\` decoded; unknown `\\x` left literal
    Enum values, `[[...]]` relations, quoted numbers/dates carry no escapes and are preserved
    verbatim. Unquoted scalars are returned as-is (a literal `\\n` stays two characters)."""
    if not _is_quoted(s):
        return s
    inner = s[1:-1]
    if s[0] == "'":
        return inner.replace("''", "'")
    return _decode_double(inner)


def _parse_scalar(val):
    """Interpret a scalar value string. Quoted -> verbatim inner string (never re-interpreted).
    Unquoted null/~/empty -> None; true/false -> bool; everything else stays a string (numbers
    included — the validator never type-checks numeric fields, so this is behaviour-neutral)."""
    if _is_quoted(val):
        return _unquote(val)
    low = val.lower()
    if low in ("", "null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return val


def _parse_flow_list(s):
    """Parse an inline flow list `[a, "b, c", d]` -> ['a', 'b, c', 'd']. Splits on commas that
    are OUTSIDE quotes, then runs each element through the SAME coercion as a scalar (unquote ->
    null/bool/keep-string) so list and scalar contexts agree (`[true, false]` -> [True, False])."""
    inner = s.strip()[1:-1]  # drop the surrounding [ ]
    items, cur = [], ""
    in_single = in_double = False
    for c in inner:
        if c == "'" and not in_double:
            in_single = not in_single
            cur += c
        elif c == '"' and not in_single:
            in_double = not in_double
            cur += c
        elif c == "," and not in_single and not in_double:
            items.append(cur)
            cur = ""
        else:
            cur += c
    if cur.strip():
        items.append(cur)
    return [_parse_scalar(x.strip()) for x in items]


def _parse_value(val):
    """A raw post-`key:` value string -> Python value. A `[[...]]` wikilink is a scalar string
    (NOT a flow list — leading `[` would otherwise mis-parse `[[entities/x]]` as `['[entities/x]']`);
    a `[...]` that is not a wikilink is a flow list; everything else is a scalar."""
    if _WIKILINK.match(val):
        return val
    if val.startswith("["):
        return _parse_flow_list(val)
    return _parse_scalar(val)


_SEQ = object()  # sentinel key marking a block-sequence `- item` line in the token stream


def _find_close(s, q):
    """Index in `s` of the flow-scalar CLOSING quote char `q`, honoring the quote style's escape
    (single: `''` is a literal quote, not a close; double: `\\"` is a literal quote). Returns None
    if `s` does not close the scalar — i.e. the quote opened here continues on the next physical
    line (a multi-line flow-folded scalar). `s` is the text AFTER the opening quote."""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if q == "'":
            if c == "'":
                if i + 1 < n and s[i + 1] == "'":
                    i += 2
                    continue
                return i
        else:  # double-quoted
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return i
        i += 1
    return None


def _fold_flow(segments, q):
    """Fold the physical-line `segments` of a multi-line flow scalar into one string, per YAML
    flow line-folding: a single line break between content lines folds to a space; each blank line
    contributes one `\\n`; then the quote style's escapes are decoded (single: `''`->`'`; double:
    `\\n \\t \\r \\" \\\\`). `segments` are already leading/trailing-stripped, blank lines kept as ''."""
    parts, pending_blanks, started = [], 0, False
    for seg in segments:
        if seg == "":
            if started:
                pending_blanks += 1
            continue
        if not started:
            parts.append(seg)
            started = True
        elif pending_blanks:
            parts.append("\n" * pending_blanks + seg)
        else:
            parts.append(" " + seg)
        pending_blanks = 0
    inner = "".join(parts)
    return inner.replace("''", "'") if q == "'" else _decode_double(inner)


def _encode_flow_scalar(s):
    """Re-encode a resolved string as a single-line double-quoted scalar so the normal scalar path
    (`_parse_value` -> `_unquote` -> `_decode_double`) decodes it back verbatim. Same escaping the
    emitter uses — keeps multi-line handling localized to the tokenizer, no new sentinel type."""
    esc = (s.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"' + esc + '"'


def _tokenize(text):
    """(indent, key, raw_value) per meaningful line — comments/blank lines dropped. A `- item`
    line becomes a (indent, _SEQ, item_text) block-sequence token; other lines with no `:` are
    skipped. A `key:` value that OPENS a quote it does not close on its line is read as a multi-line
    flow-folded scalar: continuation lines are consumed RAW (never comment-stripped — an inner ` #`
    is data, not a comment) until the quote closes, then folded + re-encoded single-line."""
    out = []
    raw_lines = text.splitlines()
    i, n = 0, len(raw_lines)
    while i < n:
        raw = raw_lines[i]
        stripped = _strip_comment(raw)
        if not stripped.strip():
            i += 1
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        content = stripped.strip()
        if content == "-" or content.startswith("- "):
            out.append((indent, _SEQ, content[1:].strip()))
            i += 1
            continue
        if ":" not in content:
            i += 1
            continue
        key, _, val = content.partition(":")
        val = val.strip()
        q = val[:1]
        if q in ("'", '"') and _find_close(val[1:], q) is None:
            segments = [val[1:].rstrip()]          # opening-line content after the quote
            i += 1
            while i < n:
                close = _find_close(raw_lines[i], q)
                if close is None:
                    segments.append(raw_lines[i].strip())
                    i += 1
                else:
                    segments.append(raw_lines[i][:close].strip())
                    i += 1
                    break
            out.append((indent, key.strip(), _encode_flow_scalar(_fold_flow(segments, q))))
            continue
        out.append((indent, key.strip(), val))
        i += 1
    return out


def _build_map(lines, idx, indent):
    """Recursively assemble the mapping at the given indent level. A bare `key:` (empty value)
    followed by `- ` items (at >= its indent) becomes a block-sequence list; followed by a
    more-indented mapping it opens a nested map; otherwise it is None."""
    d = {}
    while idx < len(lines):
        ind, key, val = lines[idx]
        if ind < indent:
            break
        if key is _SEQ:
            idx += 1  # orphan sequence item with no owning key at this level; skip defensively
            continue
        if ind > indent:
            idx += 1  # defensive: malformed over-indent, skip
            continue
        if val == "":
            nxt = lines[idx + 1] if idx + 1 < len(lines) else None
            if nxt is not None and nxt[1] is _SEQ and nxt[0] >= ind:
                items = []
                idx += 1
                while idx < len(lines) and lines[idx][1] is _SEQ and lines[idx][0] >= ind:
                    items.append(_parse_scalar(lines[idx][2]))
                    idx += 1
                d[key] = items
            elif nxt is not None and nxt[0] > ind:
                child, idx = _build_map(lines, idx + 1, nxt[0])
                d[key] = child
            else:
                d[key] = None
                idx += 1
        else:
            d[key] = _parse_value(val)
            idx += 1
    return d, idx


def _parse_yaml(text):
    """Parse the YAML subset into nested dicts. Returns {} for empty input."""
    lines = _tokenize(text)
    if not lines:
        return {}
    data, _ = _build_map(lines, 0, lines[0][0])
    return data


# ─────────────────────────── validation ───────────────────────────

def load_schema(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return _parse_yaml(fh.read())


def _check_relation(key: str, value) -> list[str]:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, str) or not _WIKILINK.match(item):
            return [f"{key}: relation must be a wikilink '[[...]]' (got {item!r})"]
    return []


def validate_frontmatter(fm: dict, schema: dict) -> list[str]:
    errors: list[str] = []
    ptype = fm.get("type")
    if ptype not in schema:
        return [f"unknown type: {ptype!r} (expected one of {sorted(schema)})"]
    rules = schema[ptype] or {}

    for key in rules.get("required", []):
        if fm.get(key) is None:
            errors.append(f"required key missing or null: {key}")

    for key, allowed in (rules.get("enums") or {}).items():
        val = fm.get(key)
        if val is not None and val not in allowed:
            errors.append(f"{key}: {val!r} not in allowed values {allowed}")

    for key in rules.get("bools", []):
        val = fm.get(key)
        if val is not None and not isinstance(val, bool):
            errors.append(f"{key}: must be a bool true/false (got {val!r})")

    for key in rules.get("relations", []):
        val = fm.get(key)
        if val is not None:
            errors.extend(_check_relation(key, val))

    for key in rules.get("dates", []):
        val = fm.get(key)
        if val is None:
            continue
        if not (isinstance(val, str) and _ISO_DATE.match(val)):
            errors.append(f"{key}: must be ISO YYYY-MM-DD (got {val!r})")

    return errors


def _extract_frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("no YAML frontmatter (file does not start with '---')")
    # The terminator is a LINE equal to '---' (the closing fence), not any '---' substring —
    # a frontmatter VALUE may legitimately contain '---' and must not truncate the parse.
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return _parse_yaml("\n".join(lines[1:i])) or {}
    raise ValueError("unterminated YAML frontmatter")


def validate_ndjson(path, schema: dict) -> list[str]:
    errors: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {i}: invalid JSON ({exc})")
                continue
            for e in validate_frontmatter(row, schema):
                errors.append(f"line {i}: {e}")
    return errors


def validate_file(path, schema: dict) -> list[str]:
    # Routes .md vs .ndjson so main() can treat every target uniformly.
    if str(path).endswith(".ndjson"):
        return validate_ndjson(path, schema)
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        fm = _extract_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    return validate_frontmatter(fm, schema)


def main(argv: list[str]) -> int:
    """usage: state_validate.py --schema <schema.yaml> (<note.md> ... | --all <dir>)"""
    if "--schema" not in argv:
        print(main.__doc__)
        return 2
    i = argv.index("--schema")
    if i + 1 >= len(argv):
        print(main.__doc__)
        return 2
    schema_path = Path(argv[i + 1])
    rest = argv[:i] + argv[i + 2:]
    # A missing/unreadable/unparseable --schema is an INVOCATION error (exit 2), NOT a validation
    # failure — it must not masquerade as a FAIL (exit 1) or a traceback.
    try:
        schema = load_schema(schema_path)
    except OSError as exc:
        print(f"usage error: cannot read --schema {schema_path}: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - any parse failure of the schema is an invocation error
        print(f"usage error: cannot parse --schema {schema_path}: {exc}")
        return 2
    all_mode = bool(rest) and rest[0] == "--all"
    if all_mode:
        base = Path(rest[1]) if len(rest) > 1 else Path.cwd()
        # _views/ holds rendered-dashboard output (Dataview etc.) with no frontmatter, not state
        # records — skip by path COMPONENT (not substring) so a literal `_views.md` file outside
        # such a dir would still be discovered (none exist today). Discovery-only: an explicitly
        # passed _views/ file argument (the `else` branch below) is still validated.
        targets = [p for p in sorted(base.rglob("*.md"))
                   if p.name != "README.md" and "_views" not in p.parts]
        targets += [p for p in sorted(base.rglob("*.ndjson")) if "_views" not in p.parts]
    else:
        targets = [Path(p) for p in rest]
    if not targets and not all_mode:
        print("usage error: no targets given and no --all <dir>")
        return 2
    # An empty target set under a VALID --all is a clean, successful empty tree (exit 0), not usage error.
    failures = 0
    for path in targets:
        try:
            errs = validate_file(path, schema)
        except Exception as exc:  # noqa: BLE001 - one malformed record must never abort the batch
            errs = [f"could not validate ({type(exc).__name__}: {exc})"]
        if errs:
            failures += 1
            for e in errs:
                print(f"FAIL {path}: {e}")
    print(f"{len(targets) - failures}/{len(targets)} PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
