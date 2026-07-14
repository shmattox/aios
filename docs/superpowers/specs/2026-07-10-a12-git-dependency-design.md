---
type: spec
project: aios
item: A12
title: Declare & optionally wire the git dependency
created: 2026-07-10
status: approved
tags: [spec, aios, setup, git, truth-in-docs, onboarding]
---

# A12 — Declare & optionally wire the git dependency

## Problem

The backlog framed A12 as a missing dependency: `/aios:setup` never establishes git,
yet the gate ("ships as a git commit") and the distill-retire clause ("runs `git add`")
appear to assume a git-tracked, committing vault.

**Code inspection contradicts that framing.** The engine's undo is entirely file-based:

- `ship.py` writes a `revert/` pointer + a `.prev.md` copy — no git.
- `rewind.py` uses a snapshot dir and atomic file moves — no git.
- `garden_distill.py` retire **moves** the husk to `raw/archive/` — no git.

There is **no `subprocess` git call anywhere in the engine tools.** `git` appears in exactly
two *prose* spots and one unrelated test fixture:

- `skills/gate/SKILL.md:72` — a prose instruction to `git add` the moved husk + relinked files.
- `engine/pipeline/PIPELINE.md:63` — descriptive "is a git commit, `revert {id}` undoes…".
- `test_brief_session.py:501` — an evidence-source label `"source": "git"` (irrelevant here).

**Conclusion:** no code path hard-fails on a non-git vault. Git is already optional at runtime;
undo works via `rewind.py` regardless. A12 is therefore two things, neither a dependency fix:

1. **A truth-in-docs gap** — the product *describes* itself as committing when it does not
   depend on git (the A50 "ships prose asserting unbuilt/other behavior" pattern).
2. **An opt-in convenience gap** — a user who *wants* version history / sync has no setup path
   to turn it on.

A12 closes both. It is a small, subtraction-leaning item plus one narrow, explicitly opt-in
setup addition.

## Decisions (brainstorm with Seth, 2026-07-10)

- **Scope:** prose fixes **+** an optional setup offer (not prose-only).
- **Yes-branch wiring — instruct, don't automate (reconciled against the ecosystem check below).**
  Setup **instructs** the git-init as the user's native step — it prints the exact `git init` +
  baseline-commit commands ready to paste, with an optional `run them now? [y/N]` that executes
  **only** on explicit yes. **BYO cadence**; AIOS owns **no** committer and registers **no**
  scheduled task; sync (remote + push) is the user's to wire. This matches our own
  `new-business-unit` convention (§Ecosystem-check) rather than diverging from it — an earlier
  "setup runs git init itself" pick was reversed once that prior art surfaced.
- **Init location:** the **vault root** (the knowledge the offer promises to version; what the
  gate's prose was about). `state/` stays untracked runtime; undo is `rewind.py` regardless.
  If the vault and env_root are the same directory, one repo covers both.

## Ecosystem-check (executed 2026-07-10 — three legs, per the plan-then-shop lesson)

Run **before** finalizing the build shape, not reconstructed from memory.

- **Anthropic / ecosystem (leg 1).** Superpowers ships `using-git-worktrees` and
  `finishing-a-development-branch` — branch/worktree tooling, **not** vault-onboarding git-init.
  No native Claude Code capability covers "optionally `git init` a data dir during onboarding"
  (it is a trivial shell sequence). **Nothing to reuse.**
- **Marketplace (leg 2).** `npx skills find "git init onboarding"` → *No skills found.*
  `npx skills find "optional git version history setup"` → *No skills found.* **Empty** (expected
  for this niche).
- **Our own skills/tools (leg 3) — the hit.** `grep` over `_tools`/`Projects/*/skills`/`Scripts`:
  - **`_tools/new-business-unit/SKILL.md:103`** — established convention, verbatim: *"Git init is
    Seth's native step — never run git from the sandbox. Tell him: `git init` + create repo +
    first push."* i.e. **instruct the human, don't automate.** This directly covers "onboard a
    dir into git" and **reshaped this spec's yes-branch** from automate → instruct.
  - **`Scripts/env-auto-sync/`** — the existing committer, but **instance infra, not product**;
    confirms AIOS-the-product should own no committer.

**Outcome:** no custom skill warranted; reuse the `new-business-unit` instruct-don't-automate
convention for the setup prompt. The build is prose edits + a small setup-skill prompt + a test.

## Design

### 1. Truth-in-docs fixes (the honesty leg)

- **`skills/gate/SKILL.md:72`** — replace the `git add` instruction with the actual mechanism:
  the gate archives the husk + relinks the references, and that move is revertible via
  `rewind.py undo-ship` / the ship revert pointer. Remove the implication that a git commit is
  what makes the ship revertible.
- **`engine/pipeline/PIPELINE.md:63`** — replace "is a git commit, `revert {id}` undoes a bad
  self-edit" with language anchored on the real primitive: a ship **records a revertible unit**
  (rewind.py snapshot / revert pointer); **git is an optional history/sync layer, not required
  for undo.**
- **One new doc line** (setup skill preamble and/or README): AIOS needs **no git to run**; git
  is an optional history + sync layer, BYO.

Guiding rule: the docs must not assert git-backed behavior the runtime does not have. Describe
the file-snapshot undo that actually exists; mention git only as the optional layer it is.

### 2. Optional wiring (the setup leg)

**Phase 4 — Interview (human-only parts).** Add one question alongside the existing auto-ship
opt-in:

> "Track your vault in git for history + sync? [y/N]"

Default is **No** (silent — matches setup's safety-default posture).

**Phase 5 — Write, scaffold, generate.** The prompt runs here, *after* the vault / profile / KB
scaffold exists, so a baseline commit (if the user runs one) captures a complete initial state.
On **yes**, setup **instructs** rather than automates — it prints the exact commands as the
user's native step (matching `new-business-unit`):

> To version your vault (optional — AIOS runs fine without it):
> ```
> git init <vault_root>
> git -C <vault_root> add -A
> git -C <vault_root> commit -m "AIOS baseline"
> ```
> Commit as you go, or wire your own cadence/hook. For sync, add a remote and push (BYO).
> AIOS does not commit for you.

Then optionally: `Run these now? [y/N]`. Setup executes the three commands **only** on an
explicit yes; on anything else it leaves them for the user to run. This "run now?" is the single
place setup ever invokes git, and only on explicit opt-in.

### 3. Idempotency & safety (Refresh-safe, per the `seed_resolve_defaults` precedent)

Relevant only when the user takes the optional `Run these now? [y/N]` (setup only ever invokes
git there). Before running:

- **Already a git repo** (`git -C <vault_root> rev-parse --is-inside-work-tree` succeeds) →
  **skip the run**: no re-init, no `add`, no commit, no clobber. Report "vault already tracked
  in git."
- **`git` not installed** (Phase 0 already detects available tools) → don't offer to run; print
  "git not found — install it, then run the commands above." Never fail the setup run; the engine
  runs fine without git.
- **User answers No to the offer, or No to `Run now?`** → print the commands and move on; nothing
  is executed (the default).

### 4. Non-git guard test (validates the "optional" claim)

A test proving a **fresh non-git vault** completes a `capture → sort → ingest → gate` cycle with
**zero git dependency** — locking in that no future change silently reintroduces a hard git
requirement. This is the regression that keeps the honesty leg honest.

- Arrange: a temp vault + minimal profile/state with **no** `.git`.
- Act: run capture → sort → ingest, then the gate's ship path on a low-risk item.
- Assert: the cycle completes; the shipped unit is revertible via `rewind.py` / the revert
  pointer; no git invocation occurred and none was required.

## Non-goals

- No auto-commit task, no remote/push automation, no commit cadence.
- Setup does not *automate* git-init; it instructs it (optional `run now?` only, opt-in) — matching
  `new-business-unit`, not diverging from it.
- No change to the undo mechanism (stays file-snapshot / rewind.py).
- No `git` calls added to the engine runtime — the setup `run now?` is the only invoker.
- No git config beyond `init` + one baseline commit, and only if the user opts to run it.

## Acceptance

- The two prose spots (`gate/SKILL.md:72`, `PIPELINE.md:63`) no longer assert git-backed
  behavior; the optional-git doc line is present — shown by diff.
- Fresh install, answer **yes** → setup prints the exact `git init` + baseline-commit commands
  (as a paste-ready native step) and offers `Run now?` — shown.
- `Run now? y` on a non-repo vault → vault becomes a git repo with one baseline commit — shown.
- `Run now?` skipped / **no** to the offer / `git` absent / vault already a repo → setup completes
  cleanly, **nothing executed**, no clobber — each shown.
- The non-git guard test passes: a fresh non-git vault completes capture → gate with no git
  dependency — shown green in the suite.
