#!/usr/bin/env python3
r"""links_only.py — the deterministic "links-only" diff guard (A102).

The Paper-Governs kb-clamp (`lane_policy`) holds EVERY familyoffice write for a human, because a FO
write can carry economic/ownership content no regex can safely classify. But a garden-connect hygiene
draft — a dead-`[[wikilink]]` repoint, a "see also" de-bloat — changes NOTHING a reader sees, so
holding it for a human is pure friction with zero safety value.

`links_only_diff(incumbent, draft)` is the provable, per-item exception: it returns True IFF the ONLY
differences between the two markdown documents live in a link's HIDDEN plumbing — a wikilink target or a
markdown-link URL. A link's VISIBLE text (a bare wikilink's target, a `[[t|alias]]` alias, a
`[label](url)` label, an image alt) is preserved through masking and compared verbatim, so a change to
any human-visible economic/ownership figure — even one hidden inside a link alias or label — fails the
diff and holds. It is a STRUCTURAL diff, not a classifier — displayed content *cannot* change on a draft
it passes — so it is strictly stronger than the "hold everything FO" default it narrows.

Fail-safe: an empty incumbent (a new page), a link inside a code region (fenced ```/~~~, inline `code`,
or a 4-space/tab-indented block — all treated as literal content), or ANY parse error returns False
(hold). The function only ever *permits* a ship; it never forces one.

Reuses the garden connect-pass regexes (`garden_neighbors._FENCE`/`_INLINE`) so the code/inline masking
matches what the connect passes emit; the wikilink/markdown-link patterns are local because the guard
must capture a link's alias/label (its visible text) — which the garden `_WIKILINK` deliberately drops.
`hygiene_auto_ship_ok` ANDs the diff with `ship._content_refusal` (A85). Zero-LLM, stdlib-only, no I/O.
"""
import difflib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# REUSE the garden connect-pass code/inline regexes verbatim (the spec's own-tools leg). garden_neighbors
# imports at stdlib cost (fastembed is lazy), so this is safe for the pure masking helpers.
from garden_neighbors import _FENCE, _INLINE

# Link patterns — LOCAL (not garden's _WIKILINK) because the guard must separate a link's VISIBLE text
# (preserved) from its HIDDEN target/URL (masked): a wikilink capturing target + optional alias, and a
# markdown link/image capturing the label + URL.
_WIKI = re.compile(r"\[\[([^\]\|]*?)(?:\|([^\]]*))?\]\]")
# URL group is [^)\s]* (no whitespace) so a link carrying an optional TITLE string — `[a](url "title")`
# — does NOT match here (the space before the title breaks it): the whole link stays literal in the
# compared body, so any change to the title's arbitrary prose fails the diff and holds. A102 review-gate
# fix (HIGH): the old `[^)\n]*` swallowed the title into the masked-away URL, letting title prose change
# freely. A title-less `[label](url)` still matches and masks normally.
_MD_LINK = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]*)\)")
# Code regions that must be treated as LITERAL content (their links are examples, never live): fenced
# blocks (backtick AND tilde), inline spans, and 4-space/tab-indented lines. Over-stashing is fail-safe
# (it only ever causes MORE holds); under-stashing would mask a literal link as live.
_TILDE_FENCE = re.compile(r"~~~.*?~~~", re.S)
_INDENT_CODE = re.compile(r"(?m)^(?: {4}|\t).*$")

# A masked link is `_LSTART <visible-text> _LEND`; two links compare EQUAL iff their visible text is
# equal (hidden target/URL may differ freely). NUL-delimited so it can never collide with real markdown.
_LSTART, _LEND = "\x00L\x00", "\x00E\x00"
_CODE_RE = re.compile(r"\x00C(\d+)\x00")
# A list item whose ENTIRE content is a single masked link WHOSE VISIBLE TEXT IS A BARE SLUG/PATH — the
# "de-bloat a dangling see-also bullet" shape permitted to be REMOVED (never inserted — see
# _body_links_only). The strict slug charset is the A102 review-gate fix (BLOCK, 2026-07-20): the old
# `[^\x00]*` accepted arbitrary prose, so an inserted/removed `- [[x|Bayview sold for $9.9M]]` bullet's
# alias smuggled (or erased) displayed economic content past the guard. A slug (`bayview-holdings`,
# `projects/clarity`, `2026-05-31-mlt-01`) has no whitespace and no prose punctuation, so no human-
# readable claim survives it.
_BULLET_SLUG = r"[A-Za-z0-9][A-Za-z0-9._/#-]*"
_LINK_BULLET = re.compile(r"^\s*[-*+]\s+" + re.escape(_LSTART) + _BULLET_SLUG + re.escape(_LEND) + r"\s*$")


def _split_fm(text):
    """-> (frontmatter block incl. its fences or '', body). Only a leading `---` line opens one —
    same rule as garden_hygiene._split_frontmatter / ship._strip_frontmatter."""
    if text.startswith("---\n") or text.startswith("---\r\n"):
        lines = text.splitlines(keepends=True)
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "".join(lines[:i + 1]), "".join(lines[i + 1:])
    return "", text


def _wiki_visible(m):
    """A wikilink -> its masked form carrying the VISIBLE text: the alias when present (`[[t|alias]]`),
    else the bare target (`[[t]]` renders its target). So a target-only repoint masks identically while
    ANY change to the displayed alias/target text differs."""
    target, alias = m.group(1), m.group(2)
    visible = alias if alias is not None else target
    return _LSTART + visible.strip() + _LEND


def _md_visible(m):
    """A markdown link/image `[label](url)` / `![alt](url)` -> masked form carrying the LABEL (visible);
    the URL (group 2) is the hidden plumbing that may change freely."""
    return _LSTART + m.group(1).strip() + _LEND


def _mask_text(s):
    """Collapse each link to `_LSTART<visible>_LEND`, so two texts that differ ONLY in a link's hidden
    target/URL compare byte-identical while any change to displayed text (incl. an economic figure in an
    alias or label) survives and differs. Code regions (fenced ```/~~~, inline `code`, indented blocks)
    are stashed verbatim FIRST and restored AFTER masking, so a link that lives in code reads as literal
    content — any change to it fails the diff (holds)."""
    codes = []

    def _stash(m):
        codes.append(m.group(0))
        return f"\x00C{len(codes) - 1}\x00"

    tmp = _FENCE.sub(_stash, s)          # ```-fenced blocks
    tmp = _TILDE_FENCE.sub(_stash, tmp)  # ~~~-fenced blocks
    tmp = _INDENT_CODE.sub(_stash, tmp)  # 4-space / tab indented code lines
    tmp = _INLINE.sub(_stash, tmp)       # inline `code` spans
    tmp = _WIKI.sub(_wiki_visible, tmp)
    tmp = _MD_LINK.sub(_md_visible, tmp)
    tmp = _CODE_RE.sub(lambda m: codes[int(m.group(1))], tmp)   # restore code verbatim
    return tmp


def _body_links_only(inc_body, dr_body):
    """True iff the masked bodies differ only in (a) 'equal' lines (a link repoint inside otherwise-
    identical text masks identically) and (b) the REMOVAL of a bare-slug link bullet or a blank line
    (de-bloat a dangling see-also). A 'replace' (visible content changed), ANY non-blank INSERT, or a
    removed line that is not a bare-slug link bullet -> False (hold).

    A102 review-gate fix (BLOCK, 2026-07-20): INSERTS are forbidden wholesale (only a blank-line insert
    passes) — an inserted line, even a link bullet, is NEW human-visible text and cannot be proven
    content-neutral; and REMOVALS are constrained to bare-slug bullets (the strict `_LINK_BULLET`) so an
    aliased economic bullet (`- [[x|balance $2.5M]]`) can neither be smuggled in nor silently erased.
    The old code allowed any `_LINK_BULLET` line (unconstrained visible text) on BOTH add and remove."""
    a = _mask_text(inc_body).split("\n")
    b = _mask_text(dr_body).split("\n")
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            return False                      # a line whose MASKED (visible) content changed = real edit
        if tag == "insert":
            # an inserted line introduces NEW displayed text (even a bare-slug bullet is a new visible
            # token) — never provably links-only. Only a blank-line insert is content-neutral.
            if not all(not ln.strip() for ln in b[j1:j2]):
                return False
        else:  # delete
            # a removed line is allowed only if blank OR a bare-SLUG link bullet (de-bloat). A removed
            # aliased/prose bullet would erase DISPLAYED content -> hold.
            if not all(_LINK_BULLET.match(ln) or not ln.strip() for ln in a[i1:i2]):
                return False
    return True


def links_only_diff(incumbent_text, draft_text):
    """Deterministic, zero-LLM. True IFF the only differences between the two markdown documents are in
    a link's HIDDEN target/URL (body + non-link frontmatter; whole-link-bullet add/remove allowed) — a
    link's VISIBLE text is compared verbatim. Fail-safe: an empty incumbent (a new page — nothing to
    diff against) or any parse error returns False."""
    if not (isinstance(incumbent_text, str) and incumbent_text.strip()):
        return False                          # no incumbent -> new content -> hold (spec: always holds)
    if not isinstance(draft_text, str):
        return False
    try:
        inc_fm, inc_body = _split_fm(incumbent_text)
        dr_fm, dr_body = _split_fm(draft_text)
        # frontmatter: masked byte-compare — a link-valued field whose only change is its hidden target
        # masks identically (ok); any changed scalar OR any changed displayed value differs (hold).
        if _mask_text(inc_fm) != _mask_text(dr_fm):
            return False
        return _body_links_only(inc_body, dr_body)
    except Exception:
        return False                          # ANY ambiguity -> hold; the guard only ever permits


def _removed_content(inc_body, dr_body):
    """The RAW (unmasked) lines the draft genuinely DELETES from the incumbent body — the only
    human-visible content a links-only draft can change (inserts are forbidden; the sole edit is a
    bare-slug bullet removal). Returned as text so the economic backstop in hygiene_auto_ship_ok can
    screen it. Only `delete` opcodes count: a raw `replace` is a hidden-target REPOINT (links_only_diff
    already proved its visible text is unchanged), NOT removed content — screening it would over-hold a
    legit repoint whose line happens to share economic vocabulary, defeating A102's whole benefit on the
    FO pages it targets. (Repoints reach here only when the masked diff had no `replace`; see
    _body_links_only.)"""
    a = inc_body.split("\n")
    b = dr_body.split("\n")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "delete":
            out.extend(a[i1:i2])
    return "\n".join(out)


def hygiene_auto_ship_ok(incumbent_text, draft_text, is_journal=False):
    """The exact predicate the gate passes to `lane_policy` as `hygiene_links_only`: the change is
    provably links-only (A102) AND content-integrity clean (A85) AND its removed content trips no
    economic tripwire (defense-in-depth). A pre-existing OR newly added injection marker fails the
    second leg even when the diff itself is links-only. Fail-safe False.

    The tripwire screen (A102 review-gate fix, HIGH) is why lane_policy can EXEMPT the links-only ship
    from its whole-body tripwire without losing the backstop: links_only_diff already proves nothing
    human-visible was added and the only removal is a bare slug, so the residual worth a human glance is
    de-bloating a link that NAMES an economic topic (`[[promissory-note-2024]]`) — screened HERE, on the
    changed content, rather than on the incumbent's own (unchanged) economic body which would false-hold
    every FO hygiene draft."""
    if not links_only_diff(incumbent_text, draft_text):
        return False
    from ship import _content_refusal          # local import avoids any import-order coupling
    if _content_refusal(draft_text, is_journal) is not None:
        return False
    from lane_policy import ECONOMIC_TRIPWIRE_RE   # local import: lane_policy never imports links_only
    _, inc_body = _split_fm(incumbent_text or "")
    _, dr_body = _split_fm(draft_text or "")
    removed = _removed_content(inc_body, dr_body)
    return not (removed and ECONOMIC_TRIPWIRE_RE.search(removed))
