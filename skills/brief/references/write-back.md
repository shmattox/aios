> Reference for `skills/brief/SKILL.md` — the tactical Notion write-back contract (§3.6 / G15e).
> Every rule here is normative. **The writer is the engine tool, not improvised MCP calls (A7).**

# Notion write-back (tactical, act-then-tell)

Distinct from pipeline approval (which goes to `gate` and writes the vault): when a chosen action implies
a TACTICAL operational update to Notion — flip a task's Status/Priority/Due, log *that* a decision was made,
toggle a LifeOS signal (reached-out / RSVP / appointment done) — the **action thread** performs it through
`${CLAUDE_PLUGIN_ROOT}/engine/tools/notion_writeback.py`, which enforces this contract as tested code
(the four rules below fire as deterministic fences, exit 3 on refusal — never re-implement them in prose
or ad-hoc MCP writes). The brief *surface* still never writes; the thread is the doer.

```
notion_writeback.py flip    --page <id> --field <name> --to <value> \
    --writable <id> [--writable <id> ...] --change-log "<env_root>/state/notion-changelog.jsonl" \
    --by <action_thread> --run-id <YYYY-MM-DD>
notion_writeback.py log-row --db <id> --title "<text>" [--field "Name=Value" ...] \
    --writable <id> [...] --change-log "<env_root>/state/notion-changelog.jsonl" --by ... --run-id ...
```
`--writable` = the profile's `notion.write.writable` ids (all groups, flattened); `--change-log` = the
profile's `notion.write.change_log` resolved under `<env_root>`. Same token path as `notion_gather.py`
(env var / Credential Manager `AIOS_NOTION_TOKEN`) — works headless.

1. **Allowlist.** Write ONLY to a DB listed in `notion.write.writable`. A DB not listed (e.g. FO Assets &
   Liabilities) is read-only — surface the change as a recommendation, never write it. *(Enforced: the tool
   refuses any db/page-parent outside `--writable`, resolving the database↔data-source id aliases.)*
2. **Content gate (`pause_economic`).** Even in an allowed DB, if the write's CONTENT is economic / ownership /
   Paper-Governs (a dollar term, a percentage, an ownership change, a papered term), STOP and ask for explicit
   approval. The allowlist says *which DBs*; this says *which content* — **content wins**. If a row MIXES a
   non-economic fact with an economic term, log the non-economic acknowledgment and PAUSE the economic term.
   *(Enforced: `lane_policy.ECONOMIC_TRIPWIRE_RE` over every outgoing field name and value; a hit refuses the
   whole call naming the fields — drop the economic term or take the row to explicit approval. Recall-biased:
   a false positive only defers to a human. The tool also refuses number/people/relation properties BY TYPE —
   amounts live there — and `flip` can only target status/select/checkbox/date, never content fields.)*
3. **Act-then-tell + receipt.** The tool **reads the field's current value first** (it becomes `old` — the
   undo anchor), does the write, **read-back-verifies it landed**, then appends ONE row to
   `notion.write.change_log`: `{ts, db, page_id, field, old, new, by, run_id}`. Reversibility = Notion page
   history + this row. Then tell {{ENTITY_NAME}} what changed, in one line (the tool's JSON output is the
   evidence — lift `old` → `new` from it).
4. **One action = one write = one receipt.** Never batch-write silently; never auto-execute (see Discipline).
