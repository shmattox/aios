---
title: A65 — Embedding-based semantic connection discovery (garden connect-pass input)
status: design
date: 2026-07-11
backlog: A65
---

# A65 — Embedding-based semantic connection discovery

## Problem

The garden stage's **connect pass** and its F8 reflection pass are the engine's only
link-discovery machinery, and they are **purely lexical/structural**. `garden_audit.py` finds
orphans (zero inbound wikilinks) and dead links by walking the filesystem; the connect pass then
proposes wikilinks, and F8 clusters pages by **shared wikilink targets (≥2), shared tags (≥2),
basename token overlap, or repeated proper nouns (≥3)** (`skills/garden/rulebook/passes-reflection.md`).

Every one of those signals is lexical or structural. A page written in **novel vocabulary** that
shares no tokens, tags, links, or proper nouns with its true topical kin will **not cluster** — and
for an orphan, the connect-pass LLM has to notice the relationship cold, reading pages in isolation.
This is a structural blind spot the current heuristics cannot close: "adjacent but differently
worded" is exactly what lexical matching misses.

The fix is **embedding-based semantic recall** — a vector-similarity neighbour index that surfaces,
per under-connected page, its nearest-by-*meaning* pages as candidate links for the LLM to judge.

## Constraints (decided)

- **Local, offline embedding only.** FamilyOffice is "Confidential — audit-grade"
  (`SecondBrain/CLAUDE.md`); garden is fact-free, zero-MCP, and runs headless (`claude -p`, weekly).
  No vault content may leave the machine → no hosted embeddings API. (Decided with Seth, 2026-07-11.)
- **Scope = orphans + weakly-linked.** Targets are orphans (0 inbound) **plus** under-connected
  pages (`< 2` inbound wikilinks, tunable). Not whole-vault pairs — that is A66's (cluster-gap) lane.
- **Within-KB only for v1.** Cross-KB semantic links defer (they touch the Personal→FO leak rule).
- **Embeddings are an *input* to judgment, never an auto-link.** Every proposal rides `lane: review`
  (all semantic findings do — the garden tier rule), through the human gate. No `auto-ship`, ever.

## Approach

A new **read-only deterministic oracle**, `engine/tools/garden_neighbors.py`, a sibling of
`garden_audit.py`. It computes a local embedding index over the curated wiki and emits, per
under-connected page, a ranked list of nearest-by-meaning neighbours. The garden LLM lifts that
list in **`# Run` step 1 (Connect)**, judges each candidate against a new rulebook pass, and
proposes wikilinks as `lane: review`.

This mirrors the existing tier split exactly: `garden_audit`/`garden_hygiene` are deterministic
oracles that **report**; the model **decides**. The embedding index is one more mechanical input to
semantic judgment. Rejected alternatives (a code auto-proposer that emits links from a raw cosine
score; reusing Obsidian Smart Connections) are covered in §Ecosystem-check.

### Components

| Piece | What |
|---|---|
| `engine/tools/garden_neighbors.py` | New read-only oracle: embed → cache → within-KB nearest-neighbour candidates for orphans + weakly-linked pages. Emits JSON. Fail-soft. |
| `engine/tools/garden_audit.py` | Minor refactor: `audit_kb` additionally exposes the per-page **inbound-count map** + **link adjacency** (backward-compatible added keys) so "what is a page" / "what's already linked" has one source of truth. |
| `skills/garden/rulebook/passes-semantic-connect.md` | New pass governing the LLM's judgment of candidates (star-topology, within-KB, Paper-Governs; `lane: review`, `recommended: hold`). |
| `skills/garden/SKILL.md` step 1 + `deploy/tasks/garden.md` | Wire the oracle into the Connect pass. |
| `state/garden/embeddings/<kb>.json` | Gitignored incremental embedding cache (derived, machine-local, regenerable). |
| `requirements.txt` (garden) | `fastembed` — the engine's **first third-party Python dependency**, a deliberate, contained posture shift (see §Dependency posture). |

### Oracle mechanics

**Embed text (per page).** Frontmatter stripped; wikilink syntax, fenced code, and inline code
removed; then `title + H1/H2 headings + body prose`, truncated to the model window (~512 tokens →
title+headings+lead for long pages, sufficient for topical similarity). Same exclusions as the
audit: `staging/`, `.templates/`, and structural pages (`index`/`log`/`README`) are neither embedded
nor suggested. `journal/` and `raw/` are excluded (episodic / immutable).

**Model.** A local ONNX embedder via **`fastembed`**, concrete default `BAAI/bge-small-en-v1.5`
(384-dim, ~130MB, no PyTorch). Weights download **once** to a machine-local cache (outside the repo);
every run after is fully offline.

**Incremental cache.** `state/garden/embeddings/<kb>.json`, keyed `page_rel → {hash: sha256(embed_text), vec: [floats]}`.
Each run: hash every page's embed-text; reuse the cached vector on a hash match, re-embed only changed
pages, drop entries for deleted pages. Steady-state weekly cost is near-zero. A corrupt cache is
rebuilt from scratch (mechanical self-heal, fix-then-tell).

**Neighbour selection.** Cosine similarity. Targets = orphans (0 inbound) + weakly-linked (`< 2`
inbound). For each target, **top-k (default 5)** neighbours **within the same KB**, above a
similarity floor (default ~0.55, tuned per model), **excluding** any page already wikilinked to/from
it and itself.

**Output contract** (JSON, like `garden_audit --json`):
```
{ "<kb>": { "<target_page_rel>": [ {"neighbor": "<rel>", "score": 0.72}, ... ] } }
```
The garden LLM lifts this, judges, proposes. Totals (candidates surfaced, links proposed) go in the
run note — a persistent "many candidates, zero proposals" reading flags a mis-tuned floor or a
skipped pass.

### Integration with the connect pass

`garden_neighbors.py` runs in `# Run` step 1 (Connect), after `garden_audit` + `garden_hygiene`. The
LLM works its per-target candidate list under the new `passes-semantic-connect.md`: for each
candidate `(page → neighbour, score)`, propose the wikilink only if a real relationship exists —
honouring **star-topology** (link to the hub, not sideways), **within-KB only (v1)**, and
**Paper-Governs**. All proposals `lane: review`, enqueued via the existing
`garden-proposals.json` → `queue_tx.py add` path.

### Dependency posture (called out)

This adds the engine's **first third-party Python dependency** (`fastembed`, which pulls
`onnxruntime` + `numpy`). The engine tools are pure-stdlib today (`garden_audit.py`: "No deps beyond
the stdlib"). This is a deliberate, contained shift — the dep is confined to `garden_neighbors.py`,
declared in a garden-scoped `requirements.txt`, and **fail-soft**: every other tool keeps working
stdlib-only, and garden itself degrades cleanly when the dep is absent (below).

### Error handling — strictly additive, never wedges the run

- **Dep missing / model not downloaded while offline** (import fails, or first run with no cached
  weights + no network) → print `SKIP: <reason>`, exit 0, empty result. Garden's existing lexical
  connect pass runs unchanged.
- **Cache corrupt** → rebuild from scratch, note the repair (fix-then-tell).
- **A page unreadable / un-embeddable** → skip it, count it, continue.

A missing embedder means *fewer suggestions this week*, never a failed run and never a blocked pipeline.

### Testing (TDD, hermetic)

- Unit tests inject a **stub embedder** (fixed vectors keyed by text) so the suite needs no model and
  stays deterministic: orphan surfaces its near neighbour; already-linked pages excluded;
  weakly-linked threshold boundary; floor cutoff; top-k cap; content-hash cache hit vs. re-embed on
  change; deleted-page eviction; **graceful SKIP when the embedder import fails**; cross-KB neighbours
  excluded (v1 within-KB rule).
- One optional integration test using real `fastembed`, **skipped by default** (marker) so CI/offline
  runs don't pull weights.
- The `garden_audit` refactor gets a regression test proving the existing orphan/dead-link output is
  unchanged.

## Acceptance

- `garden_neighbors.py` emits within-KB nearest-neighbour candidates for orphans + weakly-linked
  pages, above a tuned floor, excluding already-linked pages; hermetic unit suite green with a stub
  embedder.
- `garden_audit` exposes inbound-count + adjacency with its prior output unchanged (regression test).
- New `passes-semantic-connect.md` pass wired into SKILL.md step 1 + `deploy/tasks/garden.md`; all
  proposals `lane: review`.
- Fail-soft verified: with the dep/model absent, the tool SKIPs (exit 0) and the garden run completes
  on the lexical path.
- Cache is gitignored and self-heals on corruption.

## §Ecosystem-check

Capability: *local, offline embedding-based semantic-neighbour discovery over the vault, feeding the
garden connect pass.* Four legs, run 2026-07-11.

### Leg 1 — Anthropic-first (native Claude Code + `anthropics/skills`)

```
skill: claude-api → "Does Anthropic offer a first-party embeddings API endpoint?"
result: No. Anthropic offers NO first-party embeddings endpoint; the recommended
        embeddings path is a hosted partner (Voyage AI). Native Claude Code exposes
        no embedding primitive; anthropics/skills ships no embedding/vector skill.
```
**Adopt?** No. The only Anthropic-blessed path is a *hosted* partner API — which violates the
decided local/offline confidentiality constraint. Nothing to adopt for an offline requirement.

### Leg 2 — Public marketplace (`skills.sh` via `find-skills`)

```
$ npx --yes skills find "embedding semantic similarity vector search"
No skills found for "embedding semantic similarity vector search"

$ npx --yes skills find "obsidian knowledge graph linking"
No skills found for "obsidian knowledge graph linking"
```
**Adopt?** No. The marketplace has no skill for local embedding / semantic vault-linking.

### Leg 3 — Our own skills + tools

```
$ rg -il "embed|vector|voyage|sentence.?transform|fastembed|faiss|hnsw" Projects/aios/engine
  → (only incidental substring matches: url_extract.py mentions "requests" in a
     generated snippet; capture_router/notion_gather import urllib. No embedding/
     vector infra.)
reuse candidates found:
  - engine/tools/garden_audit.py  → inventory walk + inbound-count + link parsing (reuse)
  - garden oracle/tier pattern (garden_audit/garden_hygiene report → LLM decides) (reuse)
  - skills/garden/rulebook/passes-reflection.md F8 lexical clustering (the capability
    this fills a gap in — structurally cannot do semantic recall)
```
**Adopt?** Reuse `garden_audit`'s walk + the oracle/tier pattern; **custom-build** the thin embedding
oracle. No existing tool does (or can) semantic recall — F8 is lexical only.

### Leg 4 — Full-service platforms / buy-vs-build (embedding engine)

The buy-vs-build's cost/lock-in fork (local vs hosted) was **already settled by the confidentiality
constraint** (audit-grade FamilyOffice content cannot leave the machine → local). What remains is a
*swappable, zero-cost* technical default — which local model/library — so this leg is a light
verified check rather than a full `deep-research` pass. Grounded, not reasoned from memory:

```
WebSearch "fastembed vs sentence-transformers local CPU embedding 2026 lightweight ONNX":
  - fastembed (qdrant): ONNX Runtime, purpose-built for CPU efficiency on low-resource
    machines; no PyTorch. Faster than transformers+sentence-transformers on tokens/sec.
  - sentence-transformers: PyTorch-first (heavy); CPU/ONNX possible but needs config.
  - Small models: MiniLM (~22M, 384-dim) / BAAI/bge-small-en-v1.5 (~130MB, 384-dim),
    ~5–14k sentences/sec on CPU. Sufficient for short-doc topical similarity.
  Sources: github.com/qdrant/fastembed, sbert.net efficiency docs,
           supermemory.ai open-embedding-model ranking.
Obsidian Smart Connections: NOT installed (community-plugins.json = linter, dataview,
  kanban, folder-notes, granola-sync, charts); needs the app running + a hosted API
  under the hood. Off the table.
```
**Adopt?** Adopt **`fastembed`** as the embedding engine (default `bge-small-en-v1.5`), verify the
exact model at build time against the same sources. Reject hosted APIs (confidentiality) and Smart
Connections (not installed, needs app + hosted API).

### Verdict

| Leg | Found | Decision |
|---|---|---|
| 1 · Anthropic-first | No first-party embeddings; hosted partner only | Reject (violates offline constraint) |
| 2 · Marketplace | No skill (2 queries, empty) | Nothing to adopt |
| 3 · Own skills/tools | `garden_audit` walk + oracle/tier pattern; F8 is lexical-only | **Reuse** walk + pattern; custom-build the oracle |
| 4 · Full-service / buy-vs-build | `fastembed` (local ONNX) vs hosted vs Smart Connections | **Adopt `fastembed`**; reject hosted + Smart Connections |

**Custom-build justification:** the thin embedding oracle is the differentiator the ecosystem lacks
for *our* fact-free, offline, gated pipeline. Everything reusable (the inventory walk, the
report→decide tier, the embedding engine) is reused; only the ~oracle glue that turns a local vector
index into `lane: review` candidates for the garden LLM is custom.

## Benchmark source

External review of Kanika (@KanikaBK) "living knowledge graph in Obsidian" thread, 2026-07-11 —
its Smart Connections layer is the semantic-recall capability our lexical connect pass structurally
lacks. Sibling seeds: A66 (missing-link-between-mature-clusters reflection pass), A67 (in-vault
Dataview health dashboards).
