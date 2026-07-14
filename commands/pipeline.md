---
description: Run the AIOS pipeline once, in-session (capture → sort → ingest → gate report)
---

Invoke the aios:inbox-capture skill, then aios:sort, then aios:ingest. Then, to report what would ship without writing canonical truth:

1. Resolve `<env_root>` per `${CLAUDE_PLUGIN_ROOT}/engine/pipeline/RESOLVE-INSTALL.md` — in-mount `state/`+`profile/` markers first, `~/.aios/config.json` second; never auto-`/aios:setup`.
2. Run: `python "${CLAUDE_PLUGIN_ROOT}/engine/tools/queue_tx.py" select "<env_root>/state/queue.json" --stage awaiting`
3. For each awaiting item, report its `lane`, `recommended`, and `rec_reason`.

Items whose lane and recommendation clear the profile's `gate.auto_ship_kbs` are marked "would-ship"; everything else is held. Finish with a one-screen summary: enqueued / drafted / would-ship / held.

**Note:** The real gate is `/aios:gate`. This command reads the verdict off the queue (no-write by construction).
