> **Scheduled native runs do NOT use this body:** the manifest marks this task `type: "script"`,
> so the runner executes `engine/tools/meetings_router_task.py` directly — profile resolution +
> the tool call, zero model. This body is the MANUAL / in-session runbook + the spec of what the
> script shell does.

You are the aios MEETINGS-ROUTER stage (`aios-meetings-router`). Each run: drain new Granola
meeting notes out of `<vault>/00_Inbox/meetings/` into `<env_root>/state/domains/<silo>/meetings/`,
sorted by each note's `folders:` frontmatter (the Granola folder). ADDITIVE + SAFE — you only
relocate meeting files (copy → verify → remove-source) and write a run log; you NEVER write the
queue, Notion, Drive, KB, or any record.

**Why this stage exists:** the Granola Obsidian plugin can only write inside the vault, but meeting
records live at env-root `state/domains/<silo>/meetings/`. This stage bridges the transient in-vault
drop-zone to the state engine, sorting by folder on the way.

**This stage is a thin wrapper around a deterministic tool.** All mechanics — walk the drop-zone,
read `folders:`/`granola_id`/`type`, leaf-match the folder against the config map (`profile/domains.yaml`
`meetings.folder_map`; unmapped/none → `meetings.default` = Personal), the copy-verify-remove move
preserving the year/month path, the run log to `state/task-logs/meetings-router/` — live in
`${CLAUDE_PLUGIN_ROOT}/engine/tools/meetings_router.py` (core) + `meetings_router_task.py` (shell),
tested in `engine/tools/tests/test_meetings_router.py`. Zero judgment here: run the tool, report what
it printed. Manual run: `python engine/tools/meetings_router_task.py --env-root <env_root> [--dry-run]`.
