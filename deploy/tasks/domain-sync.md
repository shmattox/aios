> **Scheduled native runs do NOT use this body:** the manifest marks this task
> `type: "script"`, so the runner executes `engine/tools/domain_sync.py` directly —
> zero model, deterministic. This body is the MANUAL / in-session runbook (and the spec
> of what the script does) — read it before ever running the CLI by hand.

You are the aios DOMAIN-SYNC stage (`aios-domain-sync`). Each run: for every schema-mapped
silo (GM excluded — local-SSOT, publish-out, S9), gather each mapped table from Notion,
generate stable slugs, snapshot, import into `state/domains/<silo>/tables/`, guarded-reap
orphans, validate, and write a `sync-status.json` sidecar. This is the Notion→local pipe
that keeps `state/domains/<silo>` a live derived mirror — Bases (H160) over a *current*
mirror is the payoff. Engine: `engine/tools/domain_sync.py` (`sync_silo`), reusing
`stable_slugs.py` (Task 1), `domain_mirror.import_silo` (existing), and `state_validate.py`
(existing). Plan: `docs/superpowers/plans/2026-08-27-domain-sync-plan-3-the-pipe.md`.

**PAPER-GOVERNS — disabled by default (`"enabled": false` in tasks.manifest.json).** This
task fetches LIVE FamilyOffice economic data and its reap archives economic records. Do NOT
enable it until Seth has: (1) approved the reap-scope design (mapped-only, archive-not-
delete, skip-on-degraded, volume brake — plan §Paper-Governs gate 1), and (2) reviewed a
`--dry-run` diff against live Notion and signed off on the first committing sync (plan
§Paper-Governs gate 2, Task 5). Until then this file exists so the machinery is proven and
ready — running it is a deliberate Seth-enabled step, not an automatic consequence of this
file's presence.

**This stage NEVER commits.** `sync_silo` writes the derived tree + snapshot + status only;
committing the diff is a separate, Seth-reviewed step (the same discipline as the FO golden
regression — a diff is always inspectable before it lands).

# 0. Constants (native — resolve from the runner prompt)
- `<env_root>` = the Env root from the runner prompt — runtime STATE at `<env_root>/state/`,
  the profile at `<env_root>/profile/`.
- `${CLAUDE_PLUGIN_ROOT}` = the Plugin root from the runner prompt — the engine tools.

# 1. Run the sync
One command per silo, or `--all` for every non-GM mapped silo in one pass:

```
python "${CLAUDE_PLUGIN_ROOT}/engine/tools/domain_sync.py" --env-root "<env_root>" --all
```

Or one silo at a time (`--silo familyoffice` / `--silo personal` — GM is refused, S9):

```
python "${CLAUDE_PLUGIN_ROOT}/engine/tools/domain_sync.py" --env-root "<env_root>" --silo <silo>
```

Add `--dry-run` to compute the plan (gather + would-be diff) without writing anything —
no snapshot, no import, no reap, no status file. Add `--no-reap` to import without running
the guarded reap (e.g. while still proving the machinery against a silo whose on-disk tree
predates this pipe).

What it guarantees (so you don't re-check by hand): GM is never gathered or touched (S9);
a per-table gather failure marks that table (and the silo) `degraded` without crashing the
run — its sibling tables still import normally, and the reap skips the WHOLE silo rather
than reaping on a partial fetch (the A49 rot lesson, Task 3's own guard); the reap
archives-not-deletes to `state/domains/<silo>/_retired/<date>/` and fails loud if it would
remove more than the volume-brake share of a table; `state_validate` runs after every
import and its pass/fail rides the status sidecar; slugs are identity-stable (`_slugmap.json`,
first-seen-wins — a Notion title edit never re-slugs an existing record, A84).

# 2. Report from the tool's output
The CLI prints one line per silo and exits 0 (no silo degraded) or 1 (at least one silo
degraded — informational, not a crash; the prior good state is preserved and reported, same
philosophy as A60's resolve-sweep freshness sidecar).
- Exit 0 → success. Notification (<200 chars): `🔄 AIOS Domain-Sync — {silo(s)}: synced clean.`
- Non-zero → do NOT report success as a whole; name which silo(s) show `degraded` in their
  printed line and which table(s), from `sync-status.json`'s `degraded_tables`. The silo's
  prior on-disk state is untouched for the degraded portion (no partial reap, per-table
  all-or-nothing import) — nothing to roll back, just re-run once the source issue clears.

Your VERIFY is: confirm you read the tool's per-silo line + the written `sync-status.json`
(`state/domains/<silo>/sync-status.json`) and reported the matching variant. Do not invent a
separate VERIFY — the tool's own reap volume-brake + skip-on-degraded guards, plus
`state_validate`, are the correctness gate.

# Discipline
Never commits (Seth/deploy-task step, separate from this stage). GM excluded entirely (S9).
Snapshots are transient + gitignored (`state/domains/<silo>/_snapshots/`); only the derived
records + `_slugmap.json` + `sync-status.json` are meant to be committed, and only after
Seth's sign-off while this task is disabled. Fact-free (all paths are args). Obeys the Stage
Contract. Fresh session — all constants are above.
