# AIOS — Automated Intelligence Operating System

A review-gated personal knowledge operating system for Claude Code. The engine is fact-free (updated on every version bump) and stateless; your knowledge, decisions, and captured items live in your vault or Notion. **Runs on Windows, macOS, and Linux.** The pipeline runs unattended on a native schedule — Windows (Task Scheduler), macOS (launchd), and Linux (cron) — or in-session on any OS, and the human-gated review phase surfaces what needs a decision today.

**The three pillars:** (1) capture incoming items from email, Drive, and your browser; (2) the autonomous pipeline — route, draft, and sort them through your knowledge system; (3) the review gate — you approve economic / ownership / Paper-Governs material before it ships, and the brief brings you the week's priority items.

---

## Install

1. **Add the plugin to your marketplace:**
   ```
   /plugin marketplace add shmattox/aios
   ```

2. **Install the plugin:**
   ```
   /plugin install aios
   ```

3. **Run onboarding** (one interview, ~5 min):
   ```
   /aios:setup
   ```

Setup detects your OS + connected tools (Notion, Gmail, Drive, Calendar), maps or scaffolds your vault, writes your profile, generates your environment CLAUDE.md, and registers seven native scheduled tasks (Task Scheduler / launchd / cron). You control every opt-in — AIOS runs vault-only with zero connectors if you want it.

---

## What setup does

**Phase 0: Detect.** Finds your OS (Windows/Mac/Linux), which MCP tools are available, and where you want AIOS to live.

**Phase 1: Discovery (connector-aware).** Maps your existing Notion databases and Drive folders to their roles — Tasks, Assets, Decision Log, etc. — or scaffolds them if they don't exist. Discovers only what you actually use.

**Phase 2: Profile interview.** Collects your identity, the domains you operate (work, personal, family office — as many as you want), any discipline rules you want enforced, and your optimization goals. **Critical: auto-ship safety gate.** For each domain, you choose whether low-risk items can ship unattended (`gate.auto_ship_kbs`). Default: **nothing auto-ships** — everything holds for your approval until you opt in.

**Phase 3: Scaffold.** Writes five profile files to `<env_root>/profile/`, scaffolds your vault folders (one KB per domain group), and generates your personalized `<env_root>/CLAUDE.md` — a routing table and session rituals customized to your setup.

**Phase 4: Smoke test.** Runs the pipeline once in-session (capture → sort → ingest) to prove everything wires, shows you a seeded example item and what the gate would do with it, then renders your brief — all without writing anything permanent.

**Windows, macOS & Linux:** Registers seven native scheduled tasks (capture-router 01:15, session-capture 01:32, ingest 02:00, gate-auto 04:00, garden Sun 03:10, resolve-sweep 06:30, brief-cache 06:50) — via Task Scheduler on Windows, launchd on macOS, a managed crontab block on Linux. You can decline scheduling and run the pipeline in-session (`/aios:pipeline`) instead.

---

## Daily flow

**Windows & macOS — Automatic.** Your seven scheduled tasks run unattended overnight:
- **01:15 — capture-router:** Moves inbox items from `auto/` to their mapped KBs.
- **01:32 — session-capture:** Packages session records (if any) into the queue.
- **02:00 — ingest:** Drafts all queued items into your vault.
- **04:00 — gate-auto:** Auto-ships low-risk items in your opted-in KBs; holds everything else.
- **Sunday 03:10 — garden:** Maintenance: renews expired views, reconciles drift.
- **06:30 — resolve-sweep:** Flags economic tasks and warms the resolution crosswalk cache so the morning brief reads warm (opt-in).
- **06:50 — brief-cache:** Precomputes the brief cache (optional; Notion-wired installs benefit most).

**If you decline scheduling — In-session.** Run the pipeline manually:
```
/aios:pipeline           # Full pipeline: capture → sort → ingest → gate (report-only)
/aios:gate              # Review + approve held items
/aios:brief             # Today's priority items + recommended actions
```

**The human gate.** Throughout the day, held items accumulate in the queue. The `brief` skill surfaces them in your chat; you approve the ones that need a decision. Approvals write to your vault as a commit — revertible by `id`.

---

## Safety model

**Axiom: nothing auto-ships by default.** The `gate.auto_ship_kbs` list starts empty. Until you explicitly opt a KB in at setup, every item holds for your review.

**Three layers protect Paper-Governs content:**

1. **The KB backstop (strongest).** Items in `familyoffice` or other restricted knowledge bases (KBs) never auto-ship under any circumstance.

2. **Fresh-context independent review (every run).** Before ANY ship — auto or manual — the gate checks the draft against its source, verifies it matches, and blocks on discipline violations (economic content without executed paper, quantitative duplication, one-home-per-fact). A CRITICAL finding rejects the item; you never see a broken ship.

3. **The economic tripwire (best-effort recall).** Scheduled auto-ship runs a regex pattern over the draft body looking for economic/ownership vocabulary. It's recall-biased, not a complete classifier — a mis-laned item with no economic jargon is the acknowledged residual, caught only by Sort + the review. **The tripwire is not a substitute for the review or the KB lane.**

**Paper-Governs hook.** Items carrying financial, legal, or ownership terms stay `legal_status: verbal` in the vault until you bring executed paper to your Drive. The gate raises a flag; you decide when to promote.

**Undo needs no git.** Every ship records a revertible unit as a file snapshot (`rewind.py` / a revert pointer); `revert` undoes it. AIOS runs fully on a non-git vault — git is an optional history + sync layer you wire yourself (setup offers to instruct it).

---

## Repository layout

```
commands/                    # Composite commands only — skills register their own /aios:* surface
  pipeline.md                # /aios:pipeline (capture → sort → ingest → gate report, in-session)
deploy/                      # OS-specific deployment
  windows/                   # Windows Task Scheduler registration + unregister scripts
  mac/                       # macOS launchd (LaunchAgent) registration + unregister scripts + the shared bash runner
  linux/                     # Linux cron registration (managed crontab block) + unregister + runner shim
  tasks/                     # Task bodies (ingest, gate, capture-router, garden, etc.)
  cloud/                     # Three /schedule cloud task bodies (ingest, gate-auto, garden) — the no-always-on-desktop variant
engine/                      # Fact-free runtime (updated on every version)
  pipeline/                  # Core pipeline specs (PIPELINE.md, QUEUE.md, STAGE-CONTRACT.md)
  kb-schema/                 # Vault taxonomy templates (per-domain scaffolding)
  tools/                     # Python utilities (capture.py, queue_tx.py, lane_policy.py)
  templates/                 # CLAUDE.md generator template
skills/                      # All AIOS skills (invoked as /aios:*)
  brief/, garden/, gate/, inbox-capture/, ingest/, rewind/
  session-capture/, setup/, sort/
.claude-plugin/              # Plugin manifest (versioned source file, hand-maintained)
README.md                    # This file
LICENSE                      # MIT
```

---

## Uninstall

**Windows:** Unregister the scheduled tasks:
```powershell
powershell -File "path/to/deploy/windows/unregister-tasks.ps1"
```

**macOS:** Remove the launchd agents:
```bash
bash path/to/deploy/mac/unregister-tasks.sh
```

**All platforms:** Remove your local config and state:
```bash
rm -rf ~/.aios
```

**Uninstall the plugin:**
```
/plugin uninstall aios
```

Your vault, Notion records, and Drive files remain untouched. Everything AIOS needs to function again lives in your vault and profile — reinstalling and re-running setup will find and rewire to the same databases.

---

## License

MIT. See [LICENSE](LICENSE) for details.
