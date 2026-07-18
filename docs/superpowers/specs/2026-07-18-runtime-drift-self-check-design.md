# Runtime-drift self-check — the brief warns when it runs a stale engine

**Date:** 2026-07-18
**Status:** design-approved (Seth, 2026-07-18 seed-walk brainstorm)
**Backlog:** aios **A90**
**Instance leg:** env-ops **H74** (env-health fold + factory-gate emit) — separate spec, shared brainstorm.

One-line: nothing compares the *running* engine to its source, so a merged engine change with no version
bump ran a days-old brief with zero signal (2026-07-14 incident). Add a cheap deterministic **runtime
fingerprint** — installed plugin vs local repo — rendered as one brief health line on mismatch, never blocks.

---

## 1. Problem

On 2026-07-14 an engine change merged **without a version bump**. `/plugin update aios` no-op'd (the
marketplace version was unchanged), the installed brief ran a pre-07-11 engine for days, and the only
symptom was a human noticing the fixed "resolve INCOMPLETE" alarm still firing. **Nothing in the system
compares the engine it is running to the engine in source.**

### 1.1 Load-bearing finding (contradicts the seed's framing)

The seed said "version self-check." **Version comparison alone would not have caught the incident** — a
no-bump change leaves `version` equal on both sides. The concrete install record proves it:

- **Installed** — `~/.claude/plugins/installed_plugins.json`, `aios@aios` entry:
  `version: 0.6.2`, **`gitCommitSha: 1ad93a0…`**, `installPath`, `lastUpdated`.
- **Repo** — `.claude-plugin/plugin.json` `version: 0.6.2`, plus the repo HEAD sha.

At the same version, only **`gitCommitSha`** diverges. So version-compare catches the *ordinary* "you
forgot to update" case; **sha-compare catches the bug this item was filed for.** The design compares both.

`claude plugin list` (native) surfaces the installed *version* but not the sha, not any repo comparison,
and not an "update available" signal — so the read is partly native but the drift detection is ours (see
§Ecosystem-check).

---

## 2. Design

### 2.1 `engine/tools/runtime_fingerprint.py` — one deterministic module

Zero-LLM, stdlib-only, fail-soft. Public entry `fingerprint(repo_root=None) -> dict`:

1. **Installed side** — read `installed_plugins.json`; take the `aios@aios` entry (prefer `scope: user`,
   else `project`). Pull `version` + `gitCommitSha`.
2. **Repo side** — locate the dev clone (see §2.3). Read `.claude-plugin/plugin.json` `version`; compute
   `git -C <repo> log -1 --format=%H -- engine/` (the last commit that touched **engine code**, not docs/backlog).
3. **Compare, layered:**
   - installed `version` ≠ repo `version` → `status: "stale-version"`,
     message `"installed engine v{iv} ≠ repo v{rv} — run /plugin update aios"`.
   - equal version, installed `gitCommitSha` ≠ repo engine-HEAD sha → `status: "stale-sha"`,
     message `"engine changed at v{v} without a version bump — bump plugin.json + reinstall"`.
   - else → `status: "clean"`.
   - no dev clone / unreadable install record → `status: "no-dev-clone"` (silent; external installs).

Returns `{status, installed:{version,sha}, repo:{version,sha}, message}`. CLI `--json` prints the dict and
exits 0 in every case (never a hard error — a fingerprint failure must never break a brief or a drain).

Scoping the sha to `git log -1 -- engine/` is what kills the naive-sha false positive: a docs-only or
backlog commit advances HEAD but not the engine sha, so it does not flag "stale."

### 2.2 Brief render (A90)

`brief_render.py` imports `runtime_fingerprint.fingerprint()` at gather and, on `status in
{stale-version, stale-sha}`, renders **one** line in the existing `pipeline_health` block (the same
delta-gated surface the A92 missed-run line uses) — never a new panel, never a block. `clean` and
`no-dev-clone` render nothing.

### 2.3 Dev-clone resolution

The brief runs from the *installed* plugin, which does not know where the dev clone is. Resolve in order,
first hit wins, silent miss:
1. `AIOS_DEV_CLONE` env var if set;
2. walk up from `env_root` (the `state/`+`profile/` dir) for a sibling `Projects/aios/.claude-plugin/plugin.json`;
3. none → `no-dev-clone`.

This keeps the feature **universally viable**: any install that sits beside its source repo gets the
check; an install with no local source degrades silently.

### 2.4 Release-gate rule (prevention) — loud-signal + convention, no new hook

Decided A (2026-07-18): the `stale-sha` line **is** the enforcement of "a merged engine change MUST bump
the version." The incident was bad only because there was *zero* signal; now three surfaces shout it (brief
here + env-health + nightly factory, the two H74 legs). The version-bump expectation is documented as a
line in the aios release checklist (`docs/`), **not** a commit hook — a hook is net-new unsynced surface
guarding a case the detection already makes loud. If the no-bump case recurs *despite* the loud signal,
that is the named trigger to add the hook.

---

## 3. Non-goals / YAGNI

- **No blocking.** Every path exits 0; drift renders a line, never halts.
- **No network / no API.** Pure local file + `git log` reads.
- **No commit hook** (see §2.4 — deferred behind a recurrence trigger).
- **No factory drain gate.** The factory runs repo-HEAD in a worktree and cannot run a stale engine; its
  H74 leg is a *detector emit*, not a precondition (see the H74 spec).

## 4. Testing

- `stale-version`, `stale-sha`, `clean`, `no-dev-clone` each from a synthetic install-record + repo pair.
- sha scoped to `engine/`: a docs-only repo commit does **not** flip `clean`→`stale-sha`.
- fail-soft: malformed `installed_plugins.json` → `no-dev-clone`, exit 0 (never raises).
- brief render: `stale-*` emits exactly one `pipeline_health` line; `clean`/`no-dev-clone` emit none.

## 5. Acceptance

`runtime_fingerprint.py --json` returns the correct status for all four cases (shown in chat); a brief run
with a synthetically-staled install record renders the drift line and a clean one renders nothing; suite
green; fresh-context review zero-CRITICAL.

---

## §Ecosystem-check

**Capability:** detect that the running (installed) plugin engine differs from its source repo, and surface
it deterministically.

### Leg 1 — Anthropic-first (native Claude Code)
```
$ claude plugin list
  ❯ aios@aios   Version: 0.6.2   Scope: project   Status: ✔ enabled
  ❯ aios@aios   Version: 0.6.2   Scope: user      Status: ✔ enabled
$ claude plugin --help   # commands: list, details, install, marketplace … no drift/compare command
```
Result: native `list` shows the installed **version** only — no `gitCommitSha`, no installed-vs-repo
comparison, no "update available." The version read is reusable; the drift detection (esp. the same-version
sha case, the actual incident) is **not** covered. `installed_plugins.json` is the richer source (carries
`gitCommitSha`), so we read the JSON directly rather than parse `list` output.

### Leg 2 — Public marketplace
```
$ npx -y skills find "plugin version drift detection"
  wshaddix/dotnet-skills@dotnet-version-detection  (54 installs)  — detects .NET SDK version in a project
```
Result: no match — the one hit detects a project's .NET SDK, unrelated to Claude-Code plugin-install drift.

### Leg 3 — Our own skills/tools
- `_tools/driftcheck/` — detects **path** drift (staged markdown referencing a non-resolving repo path); no
  version/install logic. Sibling pattern, nothing to reuse for version drift.
- `_tools/env-maintenance/` (env-health) — no version fingerprint today (its checks are artifact pruning,
  tree survey, sync log). This spec's instance leg (H74) *adds* the fingerprint fold here.
- `Scripts/factory-gate/factory_gate.py` — already imports a sibling repo's tool via `sys.path`
  (`compile_goals`); the same pattern lets env-side consumers shell this module. Reused as the H74 leg-2 host.

Result: no existing version-drift tool to reuse; env-health + factory-gate are the *hosts* for the instance
legs, driftcheck is the sibling. Genuine build.

### Leg 4 — Full-service platforms
N/A — reading local install-state (`installed_plugins.json`) + local git. No external platform involved.

| Leg | Verdict |
|---|---|
| Anthropic-first | Partial — native shows installed version; no sha/compare/update-available. Read JSON directly. |
| Marketplace | No match. |
| Own skills/tools | No reuse for version drift; env-health/factory-gate are hosts (H74), driftcheck the sibling. |
| Platforms | N/A. |

**Decision:** custom-build the ~20-line `runtime_fingerprint.py` (the thin differentiator the ecosystem
lacks); reuse the native version read's source (`installed_plugins.json`) and the established cross-repo
`sys.path`/subprocess pattern for the env consumers.
