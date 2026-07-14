# aios-resolve-sweep (native, type: script, opt-in)

Overnight resolve pre-sweep (A34, thin deterministic slice). This is a **`type: script`** task: the
runner invokes `engine/tools/resolve_sweep_task.py --env-root <env_root>` directly — **no model in
the loop**, so this body is documentation, not a prompt.

**What it does.** Reads the profile `resolve:` block (A33-seeded keywords + `cache_dir`, optional
`entities_dir`), gathers open tasks from `resolve.task_source_dbs` (the Notion **data-source**
`collection://` ids — NOT `notion.task_views`, which are `view://` ids the REST API 404s on) headless
via `notion_gather.py`, flags the economic ones (`resolve_sweep.py`), attaches each flagged task's
crosswalk candidate refs from a matching entity page (`resolve_fetch.py`), and writes
`<cache_dir>/sweep.json` with a content hash so an unchanged task set stays **warm** (no rewrite).
Emits one `resolve-sweep` context-log line.

It does **not** fetch raw Drive bytes — that model/MCP step stays at brief time, which now reads a
warm, pre-flagged cache instead of discovering flags blind (the A31 finding).

**Degrades cleanly.** No Notion token / no configured task view / gather failure → a clean no-op
(`source: none`, `flagged_count: 0`, exit 0). Nothing to hand-tend.

**Opt-in.** Shipped `enabled: false`. Register on demand:
`powershell -File deploy\windows\register-optional-task.ps1 -TaskId aios-resolve-sweep -EnvRoot "<env_root>" -PluginRoot "<plugin_root>"`
Reverse with `Unregister-ScheduledTask -TaskName 'AIOS aios-resolve-sweep'`.
