# sanitize_check: tree + history tiers + pre-push teeth (A79) — design

**Date:** 2026-07-18 · **Status:** approved by the operator (chat — "let's get A79 out"; the
seed's shape + method rules were already decided 2026-07-15, `Memory/decisions.md` in the
operator env) · **Owner:** aios engine
**Origin:** the guard scans ONE file by default (`./BACKLOG.md`) and is wired to no hook, so a
leak reaching any other tracked file — or reaching a *commit* — is invisible to every guard we
own. This class has now forced **four** history purges (2026-07-07, two on 2026-07-15, one on
2026-07-18); each was found by a human or an ad-hoc scan, never by the guard. Tree-clean ≠
history-clean: a name added in one commit and scrubbed in the next leaves a clean tree and a
dirty public history forever.

## Method rules (load-bearing, inherited from the 2026-07-15 incident notes)

The historical scan traps all came from using git's own regex machinery (`-S`/`-G` +
`--all-match` binding to `--grep`, `-G` dying on inline `(?i)` and a caller reading the ERROR as
zero hits). **This design sidesteps git regexes entirely:** the history tier streams `git log -p`
once and scans **added lines** with the SAME compiled Python patterns the file tier uses — one
pattern engine, no translation layer. Two absolute rules survive from the incident: (1) any git
subprocess failure is an ERROR (exit 2), never "clean"; (2) a scan that inspected zero
commits/zero files is an ERROR, never "clean" (the existing zero-files guard generalized).

## §1 — `--tree`: scan every tracked text file

`sanitize_check --tree` scans `git ls-files` output (NUL-separated; binary files skipped by a
null-byte sniff on the first 8KB). The one-file default stays for back-compat but prints a
one-line nudge recommending `--tree`. This kills the A79(A) gap (docs/, skills/, engine/ were
all unscanned) and the false-"dormant" class from gitignore-blind search tools — `git ls-files`
is the actual public surface.

## §2 — Inline allowlist: `sanitize:allow`

A line containing the literal marker `sanitize:allow` (in a comment) is exempt from findings —
the `drift:ignore` convention, reused. Required because the repo legitimately carries
instance-agnostic examples (a reference doc's anonymized item ids, a test fixture's synthetic
silo id) that would otherwise permanently block a wired hook. **File-level form:** a file whose
first 10 lines carry `sanitize:allow-file` (with a stated reason) is exempt as a whole — for
fixture-dense test files and archived design docs whose *purpose* is synthetic examples (the
first real tree scan found 159 findings, 112 in seven test files; line-markers there would be
pure noise). The history tier honors the file-level marker by the file's CURRENT working-tree
state (a committed, reviewed declaration; a file absent from the tree gets no exemption — noted
soundness trade, acceptable because the marker itself is diff-reviewed). Neither form ever
suppresses the EIN pattern (a real tax id is never an acceptable example — use the lettered
placeholder instead).

## §3 — `--history RANGE`: scan committed patches

`sanitize_check --history <range>` (e.g. `origin/main..HEAD`, or `--history=--all` pre-release —
the `=` form is required for the `--all` value)
runs `git log -p --no-color <range>`, tracks current commit/file while streaming, and scans only
**added** lines (`+…`, not `+++`) with the full pattern set (structural + instance roster).
Findings print `commit-short:file: [pattern] match`. Exit semantics: 0 clean · 1 findings ·
2 ERROR (git failed, range empty, zero patches streamed — the fail-loud rules above). The
`sanitize:allow` marker applies to added lines the same way. Summary line always reports
`scanned N commits / M added lines` so "clean" is auditable.

## §4 — Pre-push teeth

Ship `engine/tools/hooks/pre-push` (POSIX sh; git-for-Windows runs sh hooks): runs
`sanitize_check --tree` and `sanitize_check --history @{u}..HEAD` (falling back to `HEAD` only —
first push — via the hook's stdin ref info). Any finding or ERROR blocks the push with the
findings printed. Install = copy to `.git/hooks/pre-push` (per-machine, like the operator env's
driftcheck); an install one-liner documented in the hook header. The pre-tag/release prose
gains: `--tree` + `--history --all` MUST be clean before tagging. Fail-open is NOT wanted here
(unlike speccheck): a leak guard that can be bypassed silently is the incident class itself —
the hook blocks, and `git push --no-verify` remains the explicit human override.

## Out of scope

- Economic-figure scanning (A35's runtime sweep owns that class).
- Rewriting history on a finding (purges stay a deliberate human-approved operation).
- The operator-env laptop's hook install (instance leg — a Watching line in the operator env).

## Acceptance

- Fixture repo tests (git init in tmp): a planted roster token in a committed patch is found by
  `--history` (shown); a clean fixture passes with a non-zero scanned-commits count (shown); an
  invalid range and a zero-commit range both exit 2, never 0 (shown); an added line carrying
  `sanitize:allow` is suppressed; the EIN pattern ignores the marker (shown).
- `--tree` scans exactly the `git ls-files` set minus binaries (fixture with a binary + an
  untracked leak file: binary skipped, untracked NOT scanned — tracked-surface-only by design,
  shown); the four known-benign repo lines carry the marker and the live repo scans clean
  (`--tree` exit 0, shown).
- The pre-push hook blocks a push introducing a planted token (demonstrated on the fixture via
  `git push` to a local bare remote, shown) and passes clean pushes.
- Existing single-file/default behavior unchanged (regression: current tests stay green).
- Full suite green; fresh-context review (not the builder) zero CRITICAL, including the dev-tier
  security leg (differential review) on the diff; engine version bumped (the /plugin-update rule).

## Ecosystem check

Capabilities: **C1** tree-wide tracked-surface scan · **C2** history/patch scan with fail-loud
semantics · **C3** inline allowlist · **C4** pre-push enforcement.

### Leg 1 — Anthropic-first

```
$ ls C:/Users/sethh/.claude/plugins/cache/claude-plugins-official/
superpowers
```

No native Claude Code or official-plugin capability scans a repo/history for custom
instance-identifier patterns. The installed security suites (differential-review, semgrep,
codeql, variant-analysis) target code vulnerabilities, not identifier leakage against a private
roster — verified by their skill descriptions in-session.

### Leg 2 — Public marketplace + standard tooling

```
$ npx skills find secret scanning git history
jwynia/agent-skills@secrets-scan        269 installs
shipshitdev/library@git-safety          138 installs
shipshitdev/library@open-source-checker 138 installs
$ gitleaks version
bash: gitleaks: command not found
```

The marketplace skills are prompt-guidance wrappers around standard secret scanners. The real
adopt candidate is **gitleaks** (the ecosystem-standard tree+history scanner with custom-rule
support). Honest assessment — REJECTED for this capability, reference-only: (a) not installed on
any env machine (a per-machine binary dependency for every install of a public plugin, vs a
stdlib-only extension of a tool that already ships); (b) its TOML rule format would fork the
existing two-tier pattern contract (shipped structural tier + private out-of-repo roster at
`state/sanitize-patterns.txt`, 50 live patterns) into a second format that must be kept in sync;
(c) its findings/exit semantics don't encode our fail-loud rules (zero-scanned = ERROR) or the
`sanitize:allow` marker, which would need wrapper glue approaching the size of the extension
itself. gitleaks remains the right answer for conventional SECRET scanning (keys/tokens), which
is not this tool's class. No cost/lock-in at stake → no deep-research pass.

### Leg 3 — Own skills/tools (the richest leg)

```
$ grep -n "def scan_text\|def load_instance_patterns\|def main\|def structural_patterns" engine/tools/sanitize_check.py
56:def structural_patterns():
61:def load_instance_patterns(path):
108:def scan_text(text, patterns):
124:def main(argv=None):
```

Everything extends the existing tested tool: C1/C2 reuse `scan_text` + the compiled pattern set
verbatim (one engine, both tiers); C2's fail-loud rules generalize the existing zero-files guard
(`sanitize_check.py:145-148`); C3 follows the operator env's `drift:ignore` marker convention;
C4 follows the driftcheck per-machine hook install pattern. The four purges' method notes
(operator env `Memory/decisions.md` 2026-07-15) are the negative-space design input — they
document exactly which git-native approaches NOT to use.

### Leg 4 — Full-service replacement

```
$ cat _tools/ecosystem-check/references/platforms.md   (validated 2026-07-10)
Wired MCP connectors: Notion, Google Drive, Gmail, ... Platforms: Composio, Pipedream, Zapier, ...
```

GitHub push protection / secret scanning covers known SECRET formats on GitHub's side, not a
private instance-identifier roster (which must never be uploaded to a third party — the roster
itself is the sensitive artifact). Not service-replaceable; the private-roster constraint is
structural.

### Verdict table

| Capability | Verdict | Source | Why |
|---|---|---|---|
| C1 tree scan | adapt-skill | own `sanitize_check.scan_text` + `git ls-files` | flag + loop, same engine |
| C2 history scan | adapt-skill | own pattern engine over `git log -p` stream | sidesteps every documented git-regex trap |
| C3 allowlist marker | adapt-skill | operator env `drift:ignore` convention | proven convention, one membership test |
| C4 pre-push hook | adapt-skill | driftcheck per-machine hook pattern; gitleaks reference-only | stdlib + sh, no new dependency |
