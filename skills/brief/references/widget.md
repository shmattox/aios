> Reference for `skills/brief/SKILL.md` — the widget/review-panel build protocol + its JSON
> schema. Used when `brief.surface` is `widget` (or {{ENTITY_NAME}} asks for the clickable panel).
> Every rule here is normative.

# Widget review panel — build protocol

Build it each run:

1. Emit the boilerplate **once, verbatim** — the `.pa-*` CSS, the `pa-panel` skeleton (Approve-all /
   Reject-all / Clear bulk row; per-row list; footer with a live command preview + "Approve selected
   → ship" / "Reject selected"), and the IIFE logic `<script>` (parses `phase-a-data`, renders rows,
   persists per-row state to `localStorage["aiosReview:"+date]`, assembles the command, fires
   `sendPrompt`). The canonical panel template is the **engine-tracked** file
   `${CLAUDE_PLUGIN_ROOT}/skills/brief/templates/review-panel.html` (ships with every install;
   fact-free) — lift it unchanged; only the `phase-a-data` block below changes per run.
2. Regenerate **only** the `<script id="phase-a-data" type="application/json">` block from the held queue items:

   ```json
   {
     "date": "YYYY-MM-DD",
     "items_count": 12,
     "lead": "one-sentence orientation; say how many hold vs approve",
     "bar": [["Held",12],["Rec: hold",8],["Rec: approve",4],["Conflicts",0]],
     "items": [
       {"id":"<queue id>","kb":"familyoffice|personal|dev","lane":"review|confirm",
        "tag":"{kb} · {lane} · rec:{recommended}","label":"<real draft title>",
        "target":"<conflict_key>","summary":"<one-line distillation of the staged draft>",
        "recommended":"hold|approve|reject","rec":"<rec_reason from ingest>"}
     ]
   }
   ```

   One `items[]` entry per held `review`/`confirm` unit — **never fabricate one the queue doesn't
   contain**, never re-parse prose for it. `label`/`summary` come from the staged draft;
   `recommended`/`rec`/`lane`/`id`/`target` come straight off the queue item.

## Command the buttons send (id-based → gate)

- Approve selected → `Approve and ship these held aios drafts to the vault — my explicit approval; run the aios gate for these ids: {id} ({label}); … .`
- Reject selected → `Reject these held aios drafts (drop from the queue, with a reason): {id} ({label}); … .`
- Untoggled rows are **held** (no command contribution); selections persist in `localStorage` so reopening remembers them. `mcp_tools` for the panel: **none** — `sendPrompt` is a global, not an MCP tool.
