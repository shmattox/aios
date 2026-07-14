---
title: "A3 — Garden Rulebook Harvest: implementation plan"
date: 2026-07-05
status: in-progress
spec: docs/superpowers/specs/2026-07-01-a3-garden-rulebook-harvest-design.md
---

# A3 build plan (from the approved design)

Security gate: DONE 2026-07-05 — `skill-security-auditor` over the 7-file vendor set in isolation:
0 CRITICAL / 0 content findings (the full os-optimizer scan's 2 CRITICALs are false-positive
pattern hits in `SKILL.md` + `passes-chroma-context-rot.md`, both outside the vendor set).

## Tasks

1. **`engine/tools/tests/test_garden_hygiene.py`** (TDD — first) — logic-mirror over a hermetic
   fixture vault, `test_garden_audit.py` pattern. Asserts:
   - dup-H1 detection (H1 slug == filename stem; structural files exempt) + the deterministic fix body;
   - frontmatter gaps (no frontmatter block / missing `type:` per the kb-schema contract; structural
     + `.templates/` + `staging/` + `journal/` exempt);
   - index-refresh candidates (content pages absent from `wiki/index.md`; the deterministic index line);
   - mechanical repoints (dead link whose stem matches exactly ONE page → repoint; 0 or ≥2 → semantic,
     NOT reported here — stays with Connect judgment);
   - lane mapping via `lane_policy` unchanged: a mechanical proposal (lane `auto-ship`) ships on a
     cleared dev KB, holds on familyoffice (kb backstop), and a semantic proposal (lane `review`)
     always holds — proving B4 needs zero lane_policy edits.
2. **`engine/tools/garden_hygiene.py`** — deterministic mechanical-tier finding-set (the cross-cutting
   hygiene row of the design table). Read-only report like `garden_audit.py`; stdlib-only; fact-free;
   `--vault-root` + `--kb-map` (+`--json`). Reuses `garden_audit.audit_kb` for dead links.
3. **`skills/garden/rulebook/`** — vendor the 7 files + a README (provenance, tier map, lane rule).
   Adapt per-pass, never wholesale-copy: strip role-discovery/`vault-roles.json` (folders come from
   the kb-schema), drop G7.1 em-dash + G7.4 repo-README, retarget F9 `Plot.md`/`{INDEX}` onto our
   fixed `wiki/index.md` + KB `CLAUDE.md` routing, convert every walk/apply into emit-a-proposal.
   Why-docs (`karpathy-llm-wiki`, `anthropic-dreams`, `anthropic-architecture`) verbatim + provenance
   header. F9 subset kept: F9.2 (index freshness), F9.3 (navigation orphans), F9.5 (folder-purpose
   duplication); F9.0/F9.1/F9.4/F9.6/F9.7 left behind (role-registry + CLAUDE.md-fitness machinery).
4. **`skills/garden/SKILL.md`** — Steps 1–3 cite their rulebook passes; new hygiene sub-step runs
   `garden_hygiene.py`; Step 6 lane rule becomes tiered per B4 (mechanical → `lane: auto-ship` +
   `recommended: approve`; semantic → `lane: review`; FO always held by the kb backstop + tripwire).
5. **`deploy/tasks/garden.md` + `deploy/cloud/garden.md`** — tool list + the VERIFY discipline line
   ("every new queue item is lane: review") updated to the tiered rule; pointers only, body stays thin.

## Acceptance mapping (BACKLOG A3)

- "whole-vault garden run produces a proposed hygiene fix-set" → `garden_hygiene.py` fixture run shown.
- "a Dev/Personal mechanical fix auto-applies, a FamilyOffice/semantic fix is held" → lane-mapping
  assertions in the test (ship_action/scheduled_ship_action decisions shown) — fixture, never live,
  per the design's Testing section.
- "logic-mirror tests over a fixture vault pass (exit codes shown)" → suite run.
- "moat untouched" → zero diffs under `queue_tx.py` / `lane_policy.py` / gate skill.
