# Resolve the install — the ONE canonical §0 (every skill, every run, first)

> Single-sourced here (A17). Skills reference this file from their `# 0 — Resolve the install`
> step instead of carrying a copy — one home, zero drift. If this protocol changes, it changes
> here and nowhere else.

**Resolve `<env_root>` — markers FIRST, config SECOND (never confuse "unreachable" with "missing").**

1. **Walk up for the in-tree markers.** Starting at cwd ITSELF and then each parent in turn, find
   the first directory that contains BOTH `state/` and `profile/` (confirm it's genuinely env_root —
   not a coincidental same-named pair — by checking `state/queue.json` OR `profile/connectors.yaml`
   exists inside it) — that signature IS `<env_root>`, no config needed. This works from any project
   subfolder, because those markers live in the tree itself.
2. **Else read `~/.aios/config.json`** (Windows: `%USERPROFILE%\.aios\config.json`):
   `{ "env_root": "<absolute path>" }`. This is the FALLBACK — used only when the marker walk-up found
   nothing (cwd sits entirely outside the env tree). In some layouts the config file lives outside the
   working tree and can be unreachable — which is exactly why markers come first.
3. **Else — genuinely unresolved.** ONLY when the marker walk-up AND a readable config BOTH fail.
   **Do NOT auto-run `/aios:setup`** — an unreachable config is NOT a missing install.
   STOP and tell the human: "Can't resolve the AIOS env_root from here. If you're in a project
   subfolder, run this from the env root — the folder that contains `state/` and `profile/`. Run
   `/aios:setup` ONLY if AIOS has never been installed."

Everything personal resolves from `<env_root>`:

- profile at `<env_root>/profile/` (`connectors.yaml`, `domains.yaml`, `identity.md`, `discipline.md`)
- runtime state at `<env_root>/state/` (the queue, ledgers, context log, caches)
- the vault root from `profile/connectors.yaml: vault` (kb folders via `vault.live_kb_map`)

Engine tools live in the PLUGIN bundle: invoke as
`python "${CLAUDE_PLUGIN_ROOT}/engine/tools/<tool>.py" …` — never a repo-relative `tools/` path.
(These ALSO live outside the working tree in some layouts — a skill that must run from a project
subfolder reads only the in-tree `state/`+`profile/`, never the plugin tools.)

(Headless deploy bodies don't read the config at all — the runner prompt hands them `<env_root>`
and the plugin root directly; this file governs the interactive skills.)
