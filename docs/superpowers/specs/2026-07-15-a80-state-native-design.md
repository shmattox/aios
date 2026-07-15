---
type: spec
topic: a80-state-native
created: 2026-07-15
status: design-approved (2026-07-15)
backlog: aios A80
blocks: domain-sync Plan 2b + Plan 3 (env `docs/superpowers/specs/2026-07-15-domain-sync-notion-to-local-design.md` rev d, S12)
---

# A80 — `state_native:`: stop the importer deleting fields no rule can reproduce

## Problem

`domain_mirror.import_silo` regenerates a record's frontmatter from scratch. `build_record`
(`engine/tools/domain_mirror.py:163`) builds `fm` fresh on every import: `type`, the declared
`notion_fields:`, any `computed_fields:`, then the derived `notion_id`/`notion_url`/`last_synced`.

**A field that is not declared is therefore not "overwritten by the source" — it ceases to exist.**

That is fine while every field comes from the source database. It is not fine here, because some
fields on these records have **no source-database origin at all**: they are authored locally, in the
mirror, by a human. For those, "the source wins" does not mean *overwritten*. It means **destroyed**.

Measured on tables that are **already mapped** — this is live behaviour today, not a hypothetical:

| table | undeclared field | records |
|---|---|---|
| `entities` | `wiki` | 11 |
| `people` | `wiki` | 7 |
| | **values a sync would silently erase** | **18** |

**The only place this class was ever recorded is a test exclusion.**
`engine/tools/tests/test_domain_mirror.py` carries
`_CURATED = {"wiki", "owner_entity", "asset"}  # shipped-only state-native curation, out of scope` —
the regression *excludes* these from its "a shipped field we forgot to map" check. A design whose
sole account of a data class is a comment in a test exclusion has not accounted for it.

The silo schema already **names** the concept in prose (`state/domains/familyoffice/schema.yaml`,
the `state-entity` block): *"NOT mapped: `wiki` — a curated cross-link … whose slug is hand-set …
and so is NOT reconstructable from the … snapshot; it is state-native curation."* A80 turns that
comment into a contract the engine enforces.

## The distinction that bounds this spec

`_CURATED` has three members, and they are **not the same kind of thing**. The test lumped them
together; the port revealed they split cleanly on one question — **can any rule reproduce it?**

| field | reproducible? | evidence | owner |
|---|---|---|---|
| `wiki` (18) | **NO** | the slug is hand-set and is not derivable from any source property | **A80 — `state_native:`** |
| `owner_entity` (27) | yes | a documented ruleset derives it from two source properties | Plan 2b — a 2-key `lookup` |
| `asset` (39) | yes | a JSON-array-string relation — needs a decode | Plan 2b — decode capability |

**`state_native:` means: no rule can reproduce this, so the engine must carry it forward.**

That test is the whole scope boundary, and it settles a question that would otherwise recur: the
generated view blocks appended to some records by a *separate renderer* are **not** state-native
either — a renderer reproduces them, so a render stage owns them (aios A81). A80 is fields only.
It never touches bodies.

## Design

One optional schema key. Three touch points. No new module, no new file.

```yaml
state-entity:
  notion_source_db: entities
  state_native: [wiki]          # NEW — no rule can reproduce these; carry them forward
  notion_fields:
    name: [Name, title]
    ...
```

```
load_silo_config   table["state_native"] = tdef.get("state_native") or []
                   + COLLISION CHECK: a field declared in BOTH state_native and
                     notion_fields/computed_fields raises ValueError at load

import_silo        dest = tdir / f"{slug}.md"
                   preserved = declared keys read from dest, when dest exists
                   build_record(..., preserved=preserved)

build_record       fm = type -> notion_fields -> computed -> PRESERVED
                        -> notion_id, notion_url, last_synced
```

**Why `preserved` lands in the computed slot, and why that matters.** On disk `wiki` sits at index
21 of 24 — after every `notion_fields` entry, before the derived trio. That is exactly where a
computed field lands. Emitting there reproduces the existing byte order, so the first sync of these
18 records shows **no diff at all**. Any other position churns 18 files for cosmetics.

**Why `import_silo` reads the file and not `build_record`.** `import_silo` already computes `dest`;
`build_record` is a pure record-builder that knows nothing about the filesystem and should keep
knowing nothing. `preserved` is an optional parameter, so no existing caller breaks and a table that
declares no `state_native` behaves exactly as it does today.

### Semantics — each pinned to evidence, not preference

| case | behaviour | why |
|---|---|---|
| key absent from `dest` | **omit** it — never emit `null` | 14 of 21 `people` records have no `wiki` key at all; emitting `null` would rewrite all 14 |
| `dest` does not exist (new record) | **omit** | same rule; there is nothing to carry |
| `dest` exists but is unreadable | **fail loud** | carrying nothing from a corrupt file is precisely the silent deletion this spec exists to prevent |
| field declared, never present anywhere | not an error | nothing to carry is a legitimate state |
| field in `state_native` **and** `notion_fields`/`computed_fields` | **ValueError at load** | it cannot be both derived and unreproducible; the schema is wrong. Matches the engine's existing fail-loud posture (unknown checkbox, dangling relation, non-mirrored `rel_source`) |
| `dry_run=True` | reads `dest`, writes nothing | reading is free; the existing dry-run contract is unchanged |

### What does NOT change

- **`state_validate` needs no change.** It already validates `wiki` — the silo schema's existing
  `relations:` list declares its *type*. `state_native:` declares its *provenance*. Different axes,
  no overlap.
- **No caller signature breaks** — `preserved` is optional.
- **A table declaring no `state_native:` is byte-identical to today.**
- **Fact-free.** The list lives in the silo schema; the engine only knows the *rule*, never which
  fields any instance considers local-canonical.

## Testing

**Real-data proof — and it closes the blind spot that hid this bug.** The hermetic regression imports
into a temp dir, where no destination exists, so carry-forward would find nothing and `wiki` would
have to stay excluded — the same exclusion that hid the class in the first place. Instead: **seed the
temp `out_dir` from the frozen golden fixture before importing.** Carry-forward then has a real
destination to read, `wiki` survives, and the comparison can finally **include** it:

- `wiki` compared **18/18** across `entities` + `people`, against real data.
- `_CURATED` shrinks from `{wiki, owner_entity, asset}` to `{owner_entity, asset}` — the exclusion
  becomes coverage.
- **Still hermetic:** seeded from the *frozen golden*, never from the live record tree. The existing
  `a72_regression_is_hermetic` tripwire must stay green.

**Fixture proofs** for the edges real data does not reach (synthetic silo, no instance facts):
- absent key → omitted, not `null`
- no destination at all (new record) → omitted
- collision (`state_native` ∩ `notion_fields`) → `ValueError` at load, before any write
- a declared `state_native` field survives a re-import unchanged while its `notion_fields` siblings
  update from the snapshot — the actual behaviour under test, on a fixture

## Ecosystem-check

Run live in-session on 2026-07-15. Every command below was executed; results are pasted from real
output.

### Leg 1 — Anthropic-first

```
$ ls ~/.claude/plugins/cache/
aios  claude-plugins-official  temp_git_...  trailofbits

$ grep -rli "frontmatter" ~/.claude/plugins/cache/claude-plugins-official/
.../superpowers/6.1.0/.opencode/plugins/superpowers.js
.../superpowers/6.1.0/.pi/extensions/superpowers.ts
.../superpowers/6.1.0/docs/plans/2025-11-22-opencode-support-design.md
```

The only hits are Superpowers' own harness-adapter files and one of its design docs — they parse
frontmatter for skill metadata. There is no Anthropic-first capability for preserving locally-authored
fields across a regeneration. No coverage.

### Leg 2 — public marketplace

```
$ npx -y skills find frontmatter preserve merge
No skills found for "frontmatter preserve merge"

$ npx -y skills find "yaml frontmatter round-trip"
garrytan/gbrain@testing      156 installs
```

Literally nothing for the first query. The second returns one unrelated general testing skill, well
under the 1K-install quality bar and not about frontmatter at all. Nothing adoptable.

### Leg 3 — our own skills and tools

```
$ grep -n "^def \(_extract_frontmatter\|_parse_yaml\|emit_frontmatter\)" engine/tools/*.py
state_validate.py:288:def _parse_yaml(text)
state_validate.py:348:def _extract_frontmatter(text) -> dict
domain_mirror.py:81:def emit_frontmatter(fields: dict) -> str

$ grep -rln "preserve|carry.forward|merge.*frontmatter|superset" engine/tools/*.py
ship.py  capture.py  capture_router.py  garden_distill.py  rewind.py  brief_session.py

$ sed -n '70,84p' engine/tools/ship.py
def _draft_supersets(draft_text, incumbent):  ... set-coverage over BODY lines
```

**This is the decisive leg.** Both halves of the I/O already exist and are already used by this very
module: `state_validate._extract_frontmatter` (which `domain_mirror` already imports at line 94) reads
a record's frontmatter, and `domain_mirror.emit_frontmatter` writes it. A80 is **glue between two
functions we already own** — read the declared keys from the destination, hand them to the builder.

`ship.py:_draft_supersets` is the closest prior art and was assessed for reuse: it is **not** a fit.
It answers a different question (is a draft BODY a superset of the incumbent's, so replace instead of
append?) — set coverage over body lines, no notion of a field, no notion of provenance. Adapting it
would mean rewriting it. Recorded so a later session does not re-litigate the same candidate.

### Leg 4 — full-service platforms

```
$ WebSearch "preserve local-only YAML frontmatter fields when regenerating markdown
             from a source of truth sync tool 2026"
guidest.com/markdown/front-matter/           general frontmatter guide
markdownlang.com/advanced/frontmatter.html   general
mystmd.org/guide/configuration               composable config re-use, not preservation
docs.github.com/.../using-yaml-frontmatter   general
openmarkapp.com/blog/markdown-frontmatter-yaml
```

The generic frontmatter ecosystem is large but answers a different question. The closest match — MyST's
"single source of truth for frontmatter to re-use across projects" — is composable *configuration*, not
preserving local-only fields across a regeneration. No tool found addresses this case. Two independent
disqualifiers regardless: the engine is deliberately **stdlib-only** (a dependency is out by
constraint), and the behaviour is 20 lines against our own record contract, so adopting a library
would cost more integration than it saves. A full `deep-research` pass was judged not warranted: no
cost, licensing, or lock-in decision rides on it, and the stdlib-only constraint is dispositive on its
own. (The obligation *does* attach where a real buy-vs-build exists — it did for the dashboard leg.)

### Verdict

| Leg | Best candidate | Verdict |
|---|---|---|
| Anthropic-first | none — hits are Superpowers' own adapters | build-because-none |
| Marketplace | none (zero results; one unrelated skill) | build-because-none |
| Own skills/tools | `_extract_frontmatter` + `emit_frontmatter` (already imported here) | **adapt-skill** |
| Full-service | generic frontmatter libs / MyST | build-because-none |

**Net: `adapt-skill`.** The reader and writer already exist inside the module that needs them. Custom
code is confined to the thin differentiator nothing off-the-shelf has: a schema-declared provenance
contract, enforced at the exact emit position that preserves byte order.

## Acceptance

- `wiki` compared **18/18** against the frozen golden (`entities` 11 + `people` 7) — shown — and
  `_CURATED` is `{owner_entity, asset}`, no longer containing `wiki`.
- `a72_regression_is_hermetic` still green: the seeding reads the **golden**, never the live tree.
- A fixture silo proves: absent key → omitted (not `null`) · no destination → omitted · collision →
  `ValueError` before any write · a `state_native` field survives a re-import while its
  `notion_fields` siblings update.
- `python -m pytest -q` exits **0**, full suite green — shown.
- A real import of a `state_native`-declaring silo produces **zero git diff** on the 18 records —
  the byte-order proof.
- `state_validate --all` still PASSes for all three silos — shown.
- A table declaring no `state_native:` is byte-identical to before (regression shown).
- A fresh-context review subagent (not the builder) reports zero CRITICAL.

## Routing

- `state_native:` in `load_silo_config`/`import_silo`/`build_record` + its tests → **AIOS** (A80).
  Universally viable: any install mirroring a source database into records that also carry locally-
  authored fields. Fact-free — the field list lives in the silo schema.
- Declaring `state_native: [wiki]` in the FamilyOffice schema, and the
  `state/domains/README.md` contract rewrite → **env-ops** (H66, instance data).

## Explicitly not in A80

- `owner_entity` (2-key `lookup`) and `asset` (JSON-array decode) — **Plan 2b**. Both are
  reproducible; neither is state-native by the test above.
- Bodies of any kind, including generated view blocks — **A81**'s render stage. A renderer
  reproduces them, so they fail the test above.
- Any change to `state_validate` — the `relations:` list already covers the type axis.
