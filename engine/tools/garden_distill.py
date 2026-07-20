#!/usr/bin/env python3
"""garden_distill.py — the deterministic ENVELOPE of the nightly distill-and-retire garden step
(design §5 stage 2). The distillation itself is model judgment in skills/garden/SKILL.md; this tool
owns the mechanical parts: enumerate candidate source stubs, classify provenance, build the gated
queue proposal, and (post-approval) relink + archive the husk with a zero-dangling verify.

Stdlib only.
Never a hard delete: retire MOVES the husk to raw/archive/, and only after the knowledge shipped.
"""
import os, sys, glob, re, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_tx
from frontmatter import read_frontmatter as _frontmatter  # the one guarded flat-frontmatter reader


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _present(path):
    return os.path.isfile(path)


def enumerate_stubs(vault, kb):
    """Every present `type: source` page under {vault}/{kb}/wiki/sources/. Returns list of
    {"slug","path","fm"}. Non-source pages and unreadable files are skipped."""
    out = []
    sources_dir = os.path.join(vault, kb, "wiki", "sources")
    for p in sorted(glob.glob(os.path.join(sources_dir, "*.md"))):
        if not _present(p):
            continue
        fm = _frontmatter(_read(p))
        if fm.get("type") == "source":
            out.append({"slug": os.path.splitext(os.path.basename(p))[0], "path": p, "fm": fm})
    return out


def stub_class(fm):
    """A56 spine — the distill class of a stub:
      'concept'   — a transferable operational idea; gets deep ingest, enhanced (fan-out) synthesis
                    distill, priority throughput, and the noise-retire fence.
      'reference' — a link / artifact / entity-fact; keeps the shallow stub + cheap fold-or-retire.
    Absent / None / unknown -> 'reference' so LEGACY stubs (pre-A56, no distill_class) are
    backward-compatible and never falsely fenced. Case-insensitive."""
    v = (fm.get("distill_class") or "").strip().lower()
    return "concept" if v == "concept" else "reference"


def select_distill_batch(stubs, cap_k):
    """A56 leg 3 — value-weighted nightly distill selection, replacing the '1 stub/night' MVP cap.
    Returns (concept_batch, concept_overflow, reference_stubs):
      concept_batch    — up to cap_k concept-class stubs, oldest-first, for SYNTHESIS-mode distill
      concept_overflow — remaining concept stubs, carried to the next night (never starved by refs)
      reference_stubs  — reference-class stubs for the cheap fold-or-retire path (no synthesis budget)
    Oldest-first = ascending (last_reconciled, slug); an empty last_reconciled sorts first (treated
    as most-overdue). cap_k <= 0 -> no concept distills this run (all concepts overflow)."""
    concept, reference = [], []
    for s in stubs:
        (concept if stub_class(s["fm"]) == "concept" else reference).append(s)
    concept.sort(key=lambda s: (s["fm"].get("last_reconciled") or "", s["slug"]))
    k = max(0, cap_k)
    return concept[:k], concept[k:], reference


def assert_noise_retire_allowed(stub_fm):
    """A56 funnel fence — a `distill_class: concept` stub MUST NOT be retired as noise (de-bloat /
    prune / bulk drain) without an enhanced-distill attempt first. The attempt is recorded either by
    a shipped distill (the normal path uses retire(), which already requires the knowledge target)
    or by an explicit `distill_attempted: no-durable-concept` note the synthesis pass writes when it
    finds nothing durable. Raises RuntimeError if a concept stub lacks that marker. Reference and
    legacy (no distill_class -> reference) stubs pass freely — the fence is opt-in via classification.
    This is distinct from retire(): retire() is the DISTILL retire (knowledge shipped); this guards
    the noise-retire paths that would otherwise bypass synthesis (the H39 leak)."""
    if stub_class(stub_fm) != "concept":
        return
    if (stub_fm.get("distill_attempted") or "").strip().lower() == "no-durable-concept":
        return
    raise RuntimeError(
        "noise-retire refused: a distill_class:concept stub cannot retire as noise without an "
        "enhanced-distill attempt — ship a distill proposal, or set "
        "'distill_attempted: no-durable-concept' after a synthesis pass finds nothing durable.")


def distill_run_metrics(concept_in, knowledge_pages_touched, reference_retired, fanout_counts):
    """A56 / Karpathy F2.8 'undigested source' tripwire, as one run-note line. Inputs are this run's
    tallies; fanout_counts = pages touched per concept distill (1 == undigested single-summary).
    Returns {'mean_fanout', 'line'}. A mean fanout of 1.0, or knowledge_pages_touched 0 against
    concept_in > 0, is the depth-insufficient signal."""
    mean = round(sum(fanout_counts) / len(fanout_counts), 2) if fanout_counts else 0.0
    line = (f"distill-depth: concept_in={concept_in} knowledge_touched={knowledge_pages_touched} "
            f"ref_retired={reference_retired} mean_fanout={mean}")
    return {"mean_fanout": mean, "line": line}


def enumerate_archive(archive_dir):
    """A56 backfill — enumerate retired `type: source` stubs in a FLAT archive dir (the H39 corpus at
    raw/archive/wiki-sources-retired-<date>/). Returns [{'slug','path','fm','class'}] — enumerate_stubs
    shape plus the A56 default class (legacy stubs have no distill_class -> 'reference'; the backfill
    RUN's model re-classifies after reading each body). Backfill/validation only — NOT the nightly path."""
    out = []
    for p in sorted(glob.glob(os.path.join(archive_dir, "*.md"))):
        if not _present(p):
            continue
        fm = _frontmatter(_read(p))
        if fm.get("type") == "source":
            out.append({"slug": os.path.splitext(os.path.basename(p))[0], "path": p,
                        "fm": fm, "class": stub_class(fm)})
    return out


def tally_classes(items):
    """Pure tally of a classified stub list (each item carries 'class'): total, per-class counts, and
    the sorted concept slugs (the distill candidates a deeper ingest recovers). Fed the model's
    per-stub class decisions by both the backfill run and the nightly metric."""
    concept = sorted(i["slug"] for i in items if i.get("class") == "concept")
    reference = [i for i in items if i.get("class") != "concept"]
    return {"total": len(items), "concept_count": len(concept),
            "reference_count": len(reference), "concept_slugs": concept}


def _stem_token_match(stem, slug):
    """True if slug appears in the filename stem as a whole hyphen/underscore/space-delimited token
    (not a bare substring) — 'delta' matches 'delta-origin' but 'note' does NOT match 'notebook'.
    Conservative on purpose: a coincidental substring must NOT count as preserved provenance."""
    return re.search(r"(?:^|[^a-z0-9])" + re.escape(slug.lower()) + r"(?:$|[^a-z0-9])",
                     stem.lower()) is not None


def provenance_check(vault, kb, stub):
    """Classify how a stub's origin is preserved before it may retire:
      raw_resolves        — raw_path resolves under the vault, OR a raw/ file whose FILENAME STEM
                            contains the slug as a whole token exists -> cite that raw.
      archive_as_new_raw  — no such raw exists -> the stub itself is the origin-of-record and will be
                            moved to raw/archive/ (lossless). The SAFE default: being unsure routes
                            here, never to a false raw_resolves.
      missing             — reserved contract value for future unsafe states; not returned today
                            (both branches above are always safe). Kept so callers switch on 3 values.
    Matching is deliberately CONSERVATIVE: a bare substring (slug inside an unrelated filename, or in
    prose) does NOT count — that would falsely preserve provenance the stub never had.
    """
    fm = stub["fm"]
    raw_path = fm.get("raw_path", "")
    if raw_path:
        cand = os.path.join(vault, kb, raw_path) if not os.path.isabs(raw_path) else raw_path
        if _present(cand):
            return "raw_resolves"
    slug = stub["slug"]
    for p in glob.glob(os.path.join(vault, kb, "raw", "**", "*.md"), recursive=True):
        stem = os.path.splitext(os.path.basename(p))[0]
        if _stem_token_match(stem, slug):
            return "raw_resolves"
    return "archive_as_new_raw"


def _body(text):
    """Return the markdown body (everything after the frontmatter block)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def extract_checklist(stub_text):
    """The durable-point checklist for a stub: every `## H2` heading and every `- bullet` (any nesting
    depth — all bullet points are durable-point candidates).
    The SKILL's merge-completeness self-check asserts each of these landed somewhere in the distilled
    knowledge page. Deterministic scaffold; the 'did it land' judgment is the model's."""
    out = []
    for line in _body(stub_text).splitlines():
        s = line.strip()
        if s.startswith("## "):
            out.append(s[3:].strip())
        elif s.startswith("- "):
            out.append(s[2:].strip())
    return out


def build_proposal(kb, stub, target_slug, provenance, draft_path, now_utc):
    """Build a gate-held distill proposal item. lane:review (propose-only, MVP); conflict_key is the
    FINAL knowledge target so N stubs merging into one page serialize as one review. id is keyed on
    kb+slug (NOT time) so a re-run is dedupe-fenced by queue_tx add (idempotency)."""
    return {
        "id": f"distill-{kb}-{stub['slug']}",
        "source": "wiki",
        "kb": kb,
        "stage": "awaiting",
        "lane": "review",
        "conflict_key": f"{kb}/wiki/knowledge/{target_slug}.md",
        "claimed_by": None,
        "claimed_at": None,
        "recommended": "approve",
        "rec_reason": f"distill {stub['slug']} -> knowledge/{target_slug} ({provenance})",
        "payload_path": draft_path,
        # draft_path duplicates payload_path here on purpose: the distilled knowledge draft IS
        # this proposal's staging draft, and queue_tx's drafted-before-awaiting guard requires
        # every item entering 'awaiting' to carry it.
        "draft_path": draft_path,
        "retire_stub": f"{kb}/wiki/sources/{stub['slug']}.md",
        "provenance": provenance,
        "first_drafted_utc": now_utc,
        "history": [],
    }


def _relink_file(path, slug, target_slug):
    """Rewrite plain [[sources/<slug>]] -> [[knowledge/<target_slug>]] in one file. Piped/aliased
    forms ([[sources/<slug>|Alias]]) are deliberately NOT rewritten here — the post-move verify
    catches any residue and fails loud, rather than this guessing an alias rewrite."""
    text = _read(path)
    new = text.replace(f"[[sources/{slug}]]", f"[[knowledge/{target_slug}]]")
    if new != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return True
    return False


def retire(vault, kb, stub_slug, target_slug, date_str):
    """POST-APPROVAL: relink inbound [[sources/<slug>]] to the knowledge target, then — only if NO
    [[sources/<slug>]] link would survive — MOVE the husk to raw/archive/wiki-sources-retired-<date>/.
    Refuses (mutates nothing) if the knowledge target has not shipped.
    ATOMIC: the dangling-link verify runs BEFORE the move, so a surviving link (e.g. a piped
    [[sources/<slug>|Alias]] the plain relinker does not rewrite) raises RuntimeError with the husk
    left in place — every link still resolves, nothing half-retired. Never a hard delete.
    The slug is matched with a non-slug-character boundary (so `alpha` never matches `alphabet`/`alpha-beta`,
    and every link terminator form — `]`, `|`, `#`, trailing space, `.md`, EOF — is caught)."""
    knowledge_target = os.path.join(vault, kb, "wiki", "knowledge", target_slug + ".md")
    if not _present(knowledge_target):
        raise RuntimeError(f"retire refused: knowledge target {kb}/wiki/knowledge/{target_slug}.md "
                           f"does not exist — retire runs only AFTER the distilled page has shipped "
                           f"(ship-first invariant). Nothing moved.")

    relinked = []
    for p in glob.glob(os.path.join(vault, kb, "wiki", "**", "*.md"), recursive=True):
        if _present(p) and _relink_file(p, stub_slug, target_slug):
            relinked.append(p)

    boundary = re.compile(r"\[\[sources/" + re.escape(stub_slug) + r"(?![a-z0-9-])")
    dangling = [p for p in glob.glob(os.path.join(vault, kb, "wiki", "**", "*.md"), recursive=True)
                if _present(p) and boundary.search(_read(p))]
    if dangling:
        raise RuntimeError(f"retire aborted (husk NOT moved; links still resolve): dangling "
                           f"[[sources/{stub_slug}]] survives in {dangling}")

    stub_path = os.path.join(vault, kb, "wiki", "sources", stub_slug + ".md")
    archive_dir = os.path.join(vault, kb, "raw", "archive", f"wiki-sources-retired-{date_str}")
    os.makedirs(archive_dir, exist_ok=True)
    archived = os.path.join(archive_dir, stub_slug + ".md")
    if _present(stub_path):
        shutil.move(stub_path, archived)      # MOVE, never delete — only reached when verify is clean
    return {"relinked": relinked, "archived": archived, "dangling": []}


from _util import utf8_stdio as _utf8_stdio


if __name__ == "__main__":
    _utf8_stdio()
    a = sys.argv[1:]
    op = a[0] if a else ""
    if op == "enumerate" and len(a) >= 3:
        for s in enumerate_stubs(a[1], a[2]):
            print(f"{s['slug']}\t{provenance_check(a[1], a[2], s)}\t{s['path']}")
    elif op == "retire" and len(a) >= 6:
        res = retire(a[1], a[2], a[3], a[4], a[5])
        print(f"retired {a[3]} -> knowledge/{a[4]}; archived {res['archived']}; "
              f"relinked {len(res['relinked'])}; dangling {len(res['dangling'])}")
    elif op == "backfill" and len(a) >= 2:
        items = enumerate_archive(a[1])
        for i in items:
            print(f"{i['slug']}\t{i['class']}\t{i['path']}")
        t = tally_classes(items)
        print(f"# tally: total={t['total']} concept={t['concept_count']} "
              f"reference={t['reference_count']} (default class shown; the backfill RUN re-classifies)")
    else:
        print(__doc__)
        sys.exit(1)
