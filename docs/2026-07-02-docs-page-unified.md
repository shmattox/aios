# AIOS Docs — Unified Page Structure & Content

**Purpose:** the corrected content and structure for the `/docs/aios` page, grounded in the GitHub README (`shmattox/aios`, v0.1.1) and coherent with the AIOS install landing page (Page B). Feed this back to Replit as the source of truth, or drop it straight into the docs site.

> **Why this rewrite exists:** the current Replit draft describes a different, largely fabricated product — API keys, Zapier, an "Intelligence Layer / Decision Gate / Operator agents" architecture, and a paste-in "master system prompt." None of that is AIOS. AIOS is a **Claude Code plugin** that installs in three commands and configures itself in one interview. Everything below reflects what the plugin actually does.

**Global corrections to apply everywhere:**

- Version is **0.1.1**, not 1.0.
- Install is a **plugin**, not pasted prompts. Remove the "System Prompt Setup" and "Agent Prompts" sections entirely (see note at the end for where prompt content *does* belong).
- **No API keys, no Zapier/Make.** Connectors are authorized in Claude's connector settings.
- The Gate routes by **stake** (review + Paper-Governs + tripwire), not a numeric confidence score.
- Automation is **Windows-only today; Mac/Linux run in-session.** Say so honestly.

---

## Revised left-nav (page sections)

1. Overview
2. Prerequisites
3. How it works
4. Install
5. What setup does
6. Daily operation
7. Safety & the Gate
8. Configuration
9. Customize
10. Troubleshooting

*(Removed from the old draft: "Core Architecture" as written, "System Prompt Setup," "Agent Prompts" — all replaced by real sections above.)*

---

## 1. Overview — What is AIOS?

AIOS is a **review-gated knowledge operating system for Claude Code**, shipped as a plugin. The engine is fact-free and stateless — it ships to you, while your knowledge, decisions, and files stay in **your own vault and Notion**. AIOS captures incoming items, routes and drafts them through your knowledge system automatically, and holds anything economic, ownership-related, or Paper-Governs for your approval before it ships.

Unlike one-off ChatGPT use, AIOS **compounds**: every item filed and every decision logged makes the system sharper. And nothing auto-ships by default — you opt in, one knowledge base at a time.

**The three pillars:**

- **Capture** — pulls incoming items from email, Drive, your browser, and your work sessions into one intake.
- **The pipeline** — routes, drafts, and sorts every item through your knowledge system automatically.
- **The review gate** — you approve economic / ownership / Paper-Governs material before it ships; the brief brings you the week's priority items.

---

## 2. Prerequisites

**Required**

- **Claude Code** — the plugin runs here.
- **A vault** — any folder of markdown. AIOS scaffolds one for you if you don't have one.

**Optional connectors** (all opt-in, authorized in Claude's connector settings — **no API keys to manage**):

- Notion — live operational state (tasks, decisions, notes)
- Gmail — capture from your inbox
- Google Drive — capture files + your source-of-truth documents
- Google Calendar — meetings

> **Correction to the old draft:** you do **not** need Claude Pro/Teams API keys, an `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, Google OAuth secrets, or Zapier/Make. AIOS is fact-free and reads your vault; connectors are wired through Claude, not through pasted keys. AIOS also runs **vault-only with zero connectors** if you prefer.

---

## 3. How it works

AIOS is a pipeline with a human gate. Items flow in one direction; nothing ships past the gate without meeting your rules.

```
AIOS Pipeline
─────────────────────────────────────────────
  CAPTURE     email · Drive · browser · sessions
     │        → one intake queue
     ▼
  SORT        route each item to the right knowledge base
     │
     ▼
  INGEST      draft the item into your vault
     │
     ▼
  GATE        fresh-context review + Paper-Governs
     │        low-risk (opted-in) ships · everything else holds for you
     ▼
  GARDEN      weekly upkeep: dedupe, distill, reconcile drift
     │
     ▼
  BRIEF       today's priorities, read back to you
```

**Where things live (canonical homes).** AIOS keeps one home per kind of fact:

- **Google Drive** — executed paper, statements, anything with legal or financial weight.
- **Notion** — live operational state: decisions, priorities, what's happening now.
- **Obsidian / markdown vault** — distilled knowledge: concepts, entities, how things connect.
- **The cache** — the agent's working memory; owns nothing, always defers.

---

## 4. Install

Three commands in Claude Code:

```
/plugin marketplace add shmattox/aios
/plugin install aios
/aios:setup
```

`/aios:setup` runs one guided interview (~5 minutes). It detects your OS and connected tools, maps or scaffolds your vault, writes your profile, and generates your environment's routing file. On Windows it also registers your scheduled tasks.

> **Prefer to inspect first?** The engine is fact-free and open — read every skill and script at [github.com/shmattox/aios](https://github.com/shmattox/aios) before installing.

---

## 5. What setup does

`/aios:setup` runs five phases and writes nothing permanent until you approve:

- **Phase 0 · Detect** — finds your OS (Windows / Mac / Linux), which tools are connected, and where AIOS should live.
- **Phase 1 · Discovery** — maps your existing Notion databases and Drive folders to their roles (Tasks, Assets, Decision Log…), or scaffolds them if absent. Discovers only what you actually use.
- **Phase 2 · Profile** — a short interview: your identity, the domains you operate (work, personal, family office — as many as you want), any discipline rules, and your goals. **Auto-ship safety gate:** for each domain you choose whether low-risk items may ship unattended. **Default: nothing auto-ships** — everything holds for your approval until you opt in.
- **Phase 3 · Scaffold** — writes your profile files, scaffolds one knowledge base per domain, and generates your personalized routing file with session rituals.
- **Phase 4 · Smoke test** — runs the pipeline once, in-session, to prove it wires: shows a seeded example item, what the gate would do with it, and renders your brief — without writing anything permanent.

---

## 6. Daily operation

| Platform | Setup & in-session use | Unattended automation |
|---|---|---|
| **Windows** | ✓ Fully supported | ✓ Five native scheduled tasks run overnight |
| **Mac & Linux** | ✓ Supported | In-session today (`/aios:pipeline`, `/aios:gate`, `/aios:brief`); native launchd/cron scheduling is in progress |

**Windows — automatic.** Five scheduled tasks run unattended:

- **01:15 capture-router** — moves inbox items to their mapped knowledge bases.
- **01:32 session-capture** — packages your work sessions into the queue.
- **02:00 ingest** — drafts all queued items into your vault.
- **04:00 gate-auto** — ships low-risk items in your opted-in KBs; holds everything else.
- **Sun 03:10 garden** — weekly maintenance: renew views, dedupe, distill, reconcile drift.

**Mac & Linux — in-session.** No native scheduler yet. Run the pipeline yourself:

```
/aios:pipeline    # capture → sort → ingest → gate (report-only)
/aios:gate        # review + approve held items
/aios:brief       # today's priorities + recommended actions
```

For hands-off overnight automation on a Mac today, use the cloud-scheduled variant or the done-for-you setup while native Mac scheduling ships.

---

## 7. Safety & the Gate

The Gate is what makes AIOS safe to trust with clients, data, and money. It classifies every output by **stake** and routes accordingly — it is **not** a numeric confidence score.

- **Nothing auto-ships by default.** The `gate.auto_ship_kbs` list starts empty. Until you opt a knowledge base in, every item holds for review.
- **Fresh-context review, every run.** Before any ship — auto or manual — a separate reviewer (never the drafter) checks the draft against its source and blocks on a CRITICAL finding. You never see a broken ship.
- **The KB backstop.** Items in restricted knowledge bases (e.g. `familyoffice`) never auto-ship under any circumstance.
- **The economic tripwire.** Scheduled auto-ship runs a recall-biased pattern over the draft for economic/ownership vocabulary; anything flagged holds. (A moat, not a complete classifier — it backs up Sort and the review, doesn't replace them.)
- **Paper-Governs.** Items carrying financial, legal, or ownership terms stay `legal_status: verbal` until you bring the executed document to Drive. The gate flags it; you decide when to promote.

> **Correction to the old draft:** replace the `decision_gate` impact/reversibility/`confidence >= 0.85` YAML — that scoring matrix isn't how AIOS works.

---

## 8. Configuration

Configuration is what `/aios:setup` writes for you — you don't hand-author YAML.

- **`profile/`** — your identity, domains, discipline rules, connector map, and gate settings.
- **`<env_root>/CLAUDE.md`** — a generated routing file: where the agent reads and writes each kind of information, plus your session rituals.
- **Per-domain knowledge bases** — one scaffolded per domain group.
- **`gate.auto_ship_kbs`** — the opt-in list of knowledge bases allowed to ship low-risk items unattended (empty by default).

Connectors are configured in Claude's connector settings, not in a config file. Re-run `/aios:setup` any time to add a connector or change a setting.

> **Correction to the old draft:** remove the hot/warm/cold `memory` tiers and `access_control` YAML — neither exists in the product.

---

## 9. Customize

- **Add domains** — work, personal, family office; as many as you want, each its own knowledge base.
- **Discipline rules** — per-domain rules the agent must follow.
- **Auto-ship opt-in** — choose which knowledge bases may ship low-risk items unattended; the rest always hold.
- **Writing/brand voice** *(optional)* — teach it your voice so drafts sound like you.

---

## 10. Troubleshooting

- **No overnight automation on Mac/Linux.** Expected today — run `/aios:pipeline` in-session, or use the cloud-scheduled variant. Native launchd/cron scheduling is on the roadmap.
- **A connector returns nothing or stale data.** Re-authorize it in Claude's connector settings, then re-run `/aios:pipeline`.
- **You want to undo a ship.** Approvals write to your vault as commits — revert by `id` with `/aios:rewind`.
- **Something looks misconfigured.** Re-run `/aios:setup`; it's safe to run again and only changes what you confirm.
- **Still stuck?** Open an issue at [github.com/shmattox/aios](https://github.com/shmattox/aios).

---

## Note on "prompts" (keeps the two pages coherent)

The old draft's "System Prompt Setup" and "Agent Prompts" sections implied AIOS is installed by pasting master/agent prompts. It isn't — the behavior lives in the plugin's skills. **Cut those sections from the plugin docs.**

If you want a "prompts you can paste to build it yourself" experience, that's a **separate page** — the 3-prompt build-your-own guide (Page A). Keep the split clean:

- **`/prompts` (Page A)** — build a lightweight version yourself with 3 prompts. No plugin.
- **`/docs/aios` (this page)** — install and run the real AIOS plugin.

That separation is what makes the whole offer coherent: prompts are the free on-ramp; the plugin is the product.
