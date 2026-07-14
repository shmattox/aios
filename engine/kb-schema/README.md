# KB schema (shipped) — how to build & maintain a Karpathy-style LLM Wiki

This is the **schema only**. AIOS ships it; each person's vault *content* is records and never ships.
A vault is instantiated by copying this taxonomy into `profile`-named folders.

## Taxonomy (per KB)

```
vault/
  raw/        inbox/ processed/ sessions/ archive/     ← capture lands here first
  wiki/       knowledge/ sources/ people/ companies/
              projects/ mocs/ journal/ staging/
              index.md  log.md  .templates/            ← knowledge/ = the durable distilled layer
                                                           (the nightly Distill step's target)
  outputs/    charts/ decks/ queries/                  ← generated artifacts
```

`knowledge/` is the durable distilled layer: the legacy Karpathy split (`concepts/`/`topics/`/`insights/`)
has folded into it, and the Distill step (below) writes distilled pages there. Humans and organizations
live under `people/` + `companies/` (the former single `entities/` split by type). A KB may add its own
page-type folders as deltas — e.g. Dev's `decisions/` (ADRs) and its kept `entities/` (software/tool/
product/system entities that fit neither `people/` nor `companies/`) — but the base scaffold above is
the shared taxonomy.

## Frontmatter contract (every wiki page)

```yaml
---
title:
type: source | knowledge | people | company | project | moc | journal   # base types; KBs may add their own (e.g. Dev: decision, entity)
explored: false            # true once the page is fleshed out, not a stub
source_tier: primary | secondary | tertiary | inferred
raw_path:                   # source-type ONLY: relative path to the raw origin under raw/.
                            # If absent, distill runs a content-search fallback; if no raw exists
                            # anywhere, the stub itself is archived-as-new-raw (never lost).
distill_class: concept | reference   # A56, source-type: concept = transferable operational idea
                                      # (deep stub + fan-out synthesis distill); reference = pointer/
                                      # artifact (shallow stub, cheap path). Absent -> reference.
legal_status: n/a | verbal | papered   # economic/ownership pages only
papered_source:            # required iff legal_status: papered
last_reconciled: YYYY-MM-DD
links: []                  # wikilinks to related pages
---
```

## Maintenance rules (shipped as method)

- **Phase A → Phase B gate.** Raw capture → drafted Phase A item (self-verify) → independent review → Phase B write. Nothing skips the gate, tools included.
- **One home per fact.** Numbers/balances live in Notion; the wiki points, never re-owns.
- **Paper-Governs hook.** No economic page promotes past `verbal` without an executed doc in Drive (`legal_status: papered` + `papered_source`). The *rule* is engine; *which* relationships it covers is `profile/discipline.md`.
- **index.md / log.md** stay current; `last_reconciled` stamps get bumped on verification.

## `sources/` is the transient distill inbox (model-Y)

`wiki/sources/` is NOT a durable layer — it is the inbox the nightly garden **drains**. Lifecycle
of a `type: source` stub:

1. Ingest lands a thin stub in `{kb}/wiki/sources/{slug}.md` with `raw_path` set.
2. The nightly garden **Distill** step folds its durable points into a `{kb}/wiki/knowledge/{target}.md`
   page (refactor-not-append), proposing the change through the review gate.
3. On approval, the knowledge page ships and the stub husk is `move`d to
   `raw/archive/wiki-sources-retired-<date>/` — provenance preserved, never hard-deleted.

The **provenance-gate** is invariant: a stub retires only after its insight is captured in
`knowledge/` AND its origin is preserved (`raw_path` resolves, or the stub is archived-as-new-raw).
