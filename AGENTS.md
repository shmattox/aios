# AGENTS.md — AIOS

**Read `../../AGENTS.md` and `../../CLAUDE.md` (the environment root) first — they apply to you.**
This repo has no `CLAUDE.md` of its own; the environment root is its brief.

AIOS is the shippable engine: the fact-free, review-gated pipeline (capture → sort → ingest →
**gate** → garden + brief), packaged as a Claude Code plugin. Engine development lands here.
Its build ledger is `BACKLOG.md` at this repo root.

Several agents drain this repo at once. Assume you are not alone in it.

## Claim before you build

```bash
../../Scripts/claim/claim.sh A7395 some-slug
```

Refuses if a branch, a merged commit, or an open PR already covers the item; otherwise pushes your
claim branch. Run it **before** writing code. Branch names start with the lowercase item id.

## `main` is push-guarded

A pre-push hook denies direct pushes to `main` and any rewrite of a branch with an open PR. Land
work through a PR, and **update a PR branch by merging `origin/main` into it, never by rebasing**.
`git push --no-verify` is Seth's deliberate override, not yours.

## `BACKLOG.md` is shared and hot

- **An item id never disappears.** Closing keeps it (`- [ ]` → `- [x]`); a vanished id is a bad
  conflict resolution, and a `ledgercheck` pre-commit hook blocks it.
- Ledger edits go in their own small PR, merged immediately, never mixed with code.
- New ids are drawn at **random from 5000–9999**, never `max+1`.
- `[GATE: human]` means never work it autonomously; `[FACTORY]` is how an item opts *in* to the
  autonomous factory. Prose gates nothing.

## Engine rules that bite

- **A must-hold output format is rendered by a deterministic engine renderer** and lifted verbatim
  by the skill — never reproduced from skill prose.
- **The queue is written ONLY through `engine/tools/queue_tx.py`.** Never hand-edit `queue.json`.
- `profile/` and `state/` are the plugin's lowercase path contract — identical across every
  install, never renamed.
- Tests are pytest (`pytest.ini` at the root). Commit with `-s`.
