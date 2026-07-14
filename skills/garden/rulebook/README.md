# Garden rulebook — the harvested judgment layer (A3)

Vendored 2026-07-05 from the Benos `os-optimizer` skill (design:
`docs/superpowers/specs/2026-07-01-a3-garden-rulebook-harvest-design.md`; security-audited
pre-import with `skill-security-auditor` — 0 findings on this set). os-optimizer split into a
**rulebook** (framework references + pass files — the hard-to-rebuild judgment content) and an
**interactive shell** (role-discovery, per-finding walk, bulk-apply, HTML dashboard). Only the
rulebook came over: the shell is the inverse of the aios moat — garden runs unattended and
**proposes through the review gate**, never applies.

## File map

| File | Layer | Framework |
|---|---|---|
| `karpathy-llm-wiki.md` | why (verbatim) | F2 — wiki lint: links, orphans, digestion |
| `anthropic-dreams.md` | why (verbatim) | F8 — reflection: contradictions, merges, promotion |
| `anthropic-architecture.md` | why (verbatim) | F9 — architecture & discoverability |
| `passes-karpathy-wiki.md` | how (adapted) | F2 checks → garden Step 1 (Connect) |
| `passes-reflection.md` | how (adapted) | F8 checks → Steps 1–3 (Connect/De-bloat/Prune) |
| `passes-general-hygiene.md` | how (adapted) | G7 mechanical checks → cross-cutting hygiene |
| `passes-architecture.md` | how (adapted) | F9 index/orphan subset → cross-cutting hygiene |
| `passes-semantic-connect.md` | how (A65) | F-SC — embedding-neighbour candidates for orphans/weakly-linked; SEMANTIC tier, `lane: review`; consumes `garden_neighbors.py --json`; SKIPs cleanly if the embedder is absent |

## The two tiers (B4 lane mapping — load-bearing)

Every finding becomes a queue proposal (`queue_tx.py add`, `source: garden`). The tier decides
the lane; `lane_policy.py` does the rest **unchanged**:

- **Mechanical** — single correct output, reversible: dup-H1 removal, frontmatter floor,
  index refresh, unique-typo link repoints. Oracle: `engine/tools/garden_hygiene.py`
  (deterministic; the model lifts its findings, never re-derives them).
  → `lane: auto-ship`, `recommended: approve`. Ships unattended **only** on KBs the profile
  clears (`gate.auto_ship_kbs`); familyoffice is always held by the kb backstop, and the
  economic tripwire floors anything that smells economic even on a cleared KB.
- **Semantic** — judgment: ALL wikilink inference, ALL F8 (contradiction/merge/stale/theme/
  promotion), stub triage, folder-purpose calls. → `lane: review`. Always human-gated, every KB
  (the review lane NEVER auto-ships — lane_policy invariant).

## The adapt contract (what changed on the way in)

Applied per-pass, not wholesale (the source assumed a single-vault, auto-apply, role-registry
model):

1. **Role-discovery stripped.** Folders resolve from the aios kb-schema
   (`engine/kb-schema/README.md`) — `knowledge/ sources/ people/ companies/ projects/ mocs/
   journal/ staging/` + per-KB deltas — never from `.claude/vault-roles.json`.
2. **Walk/apply → propose.** Every "user confirms, agent applies" is now "write the fix as a
   staging draft + enqueue"; application is the gate's job.
3. **F3 (em-dash/caveman) dropped** entirely (B3 — rejected). F1/F4/F5/F6 → v2, not vendored.
4. **F9 retargeted**: the folder-index convention is FIXED at `wiki/index.md` (+ `log.md` as
   history, never navigation); routing is the KB's `CLAUDE.md`; no `Plot.md`.
5. **Never touched by any pass:** CLAUDE.md, SKILL.md, `_schema/`, `raw/` content (immutable
   canon), anything still `awaiting` in the queue, and the moat
   (`queue_tx.py`/`lane_policy.py`/gate).
