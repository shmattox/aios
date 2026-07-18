# Paper-evidence verifier — point the verifier at the Paper-Governs hold class

**Date:** 2026-07-18
**Status:** design-approved (Seth, 2026-07-18 seed-walk brainstorm)
**Backlog:** aios **A75** (absorbs **A76**)
**Class:** gate value / Paper-Governs adjacent (advisory only — never an auto-ship signal)

One-line: economic/ownership holds are held because a drafted claim must be checked against its
executed paper — a read the agent can run but a human does by hand per item today. Attach a
`paper_evidence` packet at ingest so the human's cost drops from "find and read the paper" to "does
this citation check out — approve/veto."

---

## 1. Problem

A Paper-Governs hold exists because no economic/ownership claim promotes past `verbal` without an
executed document (`legal_status: papered` + `papered_source`). Today the human performs the entire
verification by hand for every hold: find the paper, open it, locate the relevant clause, compare it to
the drafted claim, decide. The seed's framing (Karpathy's line) is literal: **this class is held not
because it is unverifiable, but because the verifier was never pointed at it.**

**Load-bearing finding (2026-07-18):** the paper is **already local**. `papered_source` points at a
markdown projection in the vault's `raw/` tier (e.g. `../../raw/inbox/gmail/<date>-<deal>-agreement.md`),
projected at capture time by the gmail/dataroom-ingest path — *not* a Drive URL. So the verifier is a
**fact-free local read**, not a Drive call: it needs no interactive auth and runs headless in the
scheduled pipeline. Drive is not a dependency.

---

## 2. Design

### 2.1 The verifier (ingest-enrich)

A new read-only enrichment leg, invoked by ingest when it drafts a Paper-Governs hold (an FO
economic/ownership draft on the `review` lane):

1. Resolve the hold's subject entity → its page's `papered_source` (and `formation_papered_source`
   where the claim is about formation).
2. **If a linked local projection is readable:** a **cheap-tier extract-and-compare** reads the
   projection, locates the passage relevant to the drafted claim, and returns
   `{doc, section, quote, verdict}` where `verdict ∈ {matches, conflicts}`. This is *retrieval, not
   final judgment* — its job is to surface and quote the passage; the human checks it. An adversarial
   stance (does the paper actually support the drafted number/term?) is the right register.
3. **If `papered_source` is absent or the projection is unreadable:** `verdict: no-paper-found` —
   fully manual, the honest residual. No Drive fallback (chosen 2026-07-18: keeps the leg fact-free,
   headless, zero Drive dependency).
4. Attach `paper_evidence: {doc, section, quote, verdict, checked_utc}` to the queue item.

The verifier **never edits** the paper or the draft — it only attaches the packet. Runs at draft time,
so evidence is ready when the hold surfaces (executed papers don't change, so it stays durable).

### 2.2 Brief render

The brief hold card renders the pre-computed `paper_evidence` beside the existing Paper-Governs flag
(`brief_render.py` ~`:221`): `doc · section · quote · verdict`. **No LLM at render time** — the brief
only reads the packet, so the daily driver stays fast and deterministic (the verification cost was paid
once at ingest).

### 2.3 A76 — decision-similarity batching (folded in)

- The brief walk **groups decision-lane holds by shared `papered_source`/entity** (all holds on one
  paper together) so one paper-read amortizes across several approvals, instead of ordering decisions
  by arrival.
- The verifier **caches a projection read** across holds sharing a `papered_source` within a run (read
  the paper once, extract each claim's passage).

### 2.4 Fixed guardrails (Paper-Governs)

- **Advisory only.** `paper_evidence` is input to the human's judgment, **never an auto-ship signal**.
  Even `verdict: matches` never auto-approves. The FO kb backstop (a `familyoffice` item never
  auto-ships) is untouched and independent of any verdict.
- **Fact-free / local / headless.** Reads the vault projection the entity already links; no Drive or
  interactive-auth dependency. `no-paper-found` is the honest residual the economic tripwire already
  knows.
- **Sensitivity.** The extracted `quote` is economic content from an already-in-vault projection —
  stored only in the private queue and rendered locally, never a public surface. The verifier is
  schema-gated to KBs carrying `legal_status`/`papered_source` (FO, Personal-Operations); it never
  runs on `gm` (Paper-Governs N/A).

### 2.5 Not a resolve-layer rebuild

The retired `resolve_brief`/`resolve_verdict` dossier layer (removed; `auto_promote` retired A55) was a
general per-item resolve pass. This is its **focused** replacement — pointed at exactly one class
(Paper-Governs holds), advisory-only, with no `auto_promote` path. Do not resurrect the general
dossier; build only the narrow verifier.

---

## 3. Data flow & touchpoints

```
ingest drafts an FO economic/ownership hold (review lane)
   │
   ▼  NEW verifier leg (read-only, cheap-tier, headless)
   ├─ resolve entity → papered_source (+ formation_papered_source)
   ├─ readable projection? ── no ──► verdict: no-paper-found
   │        │ yes
   │        ▼  extract-and-compare (cache per papered_source)
   │     {doc, section, quote, verdict: matches|conflicts}
   ▼
attach paper_evidence:{doc,section,quote,verdict,checked_utc} to the queue item
   │
   ▼
brief hold card RENDERS the packet (no LLM at render)  +  walk groups holds by shared paper (A76)
```

**aios engine:** a new verifier tool (called by the `ingest` skill/stage); the `paper_evidence` queue
field; `brief_render` hold-card render + the walk-grouping (A76). No env wiring; the familyoffice-specificity is
schema/profile-driven data, not code.

---

## 4. Testing

- **matches:** a fixture entity with a `papered_source` projection stating a term, a draft asserting the
  same term → `verdict: matches` with the quoted clause (test shown).
- **conflicts:** a draft asserting a term the projection contradicts → `verdict: conflicts` with the
  quoted clause (the high-value catch).
- **no-paper-found:** an entity with no `papered_source`, or a dangling/unreadable projection path →
  `verdict: no-paper-found`, fully manual, no crash (test shown).
- **advisory invariant:** a `matches` verdict does NOT change the lane/ballot — the item still holds for
  human approval (regression asserting the FO backstop is verdict-independent).
- **A76:** two holds sharing one `papered_source` read the projection once (cache hit shown); the brief
  walk orders them adjacently.
- Full suite green; fresh-context review before ship (Paper-Governs adjacent → the `review-gate`
  Workflow, per the env escalation rule — evidence is advisory input, so the reviewer confirms the leg
  can never become an auto-ship signal).

---

## Ecosystem check

Run live in-session on 2026-07-18. Every command executed; results pasted from real output. Capability
shopped: a headless leg that verifies a drafted economic claim against its ONE linked local
executed-document projection, advisory-only.

### Leg 1 — Anthropic-first

```
$ grep -rliE "claim.?(verif|check)|fact.?check|citation.?(check|verif)" \
    ~/.claude/plugins/cache/claude-plugins-official/
→ only unrelated superpowers docs/specs (the word "evidence" in review prose); no verifier tool
```

No claim-vs-source verifier in `claude-plugins-official`. The suite's review skills verify *findings*,
not a drafted claim against a linked document.

### Leg 2 — public marketplace

```
$ npx -y skills find "claim verification against source"
jwynia/agent-skills@fact-check                    536 installs
indranilbanerjee/…@verify-claims                   94 installs
$ npx -y skills find "fact check citation"
elvisun/newsjack@fact-check                        384 installs
anthropics/claude-for-legal@brief-section-drafter  338 installs

$ WebFetch skills.sh/jwynia/agent-skills/fact-check
→ "systematic verification of claims in generated content … a separate cognitive pass … external
   grounding where possible … an integrated agent capability" (not a headless pipeline library;
   not anchored to a specific source document).
```

Real, well-installed fact-check skills exist, but they are **general claim-checkers** (verify generated
content against open/general grounding) delivered as **runtime agent capabilities**. Reference-only by
both shape and scope: A75 is a *headless* ingest leg anchored to the entity's ONE `papered_source`
projection, producing an *advisory Paper-Governs* verdict — a session-invoked general fact-checker
cannot be the pipeline leg, and its job (broad grounding) is not ours (a specific linked clause). Their
adversarial-stance idea is adopted as design reference (§2.1).

### Leg 3 — our own skills and tools

```
$ ls ../../_tools/dataroom-ingest/convert.py
../../_tools/dataroom-ingest/convert.py        # already projects executed docs → local markdown
$ sample papered_source value
papered_source: ../../raw/inbox/gmail/<date>-<deal>-agreement.md   # LOCAL projection, not a Drive URL
```

Strong reuse — the enabling work is **already done**: `dataroom-ingest`/gmail capture already projects
executed papers into `raw/` and the FO schema already links them via `papered_source`, so the verifier
reuses that projection (no new ingestion) + the pipeline's existing ingest-enrich pattern + the
cheap-tier model-routing convention. The build is confined to the thin extract-and-compare + the
`paper_evidence` field + the render.

### Leg 4 — full-service platforms

```
$ external service for verifying a private economic claim against a private executed document?
none — a hosted fact-check/verification API would ship confidential FO paper content off-box, violating
the Paper-Governs confidentiality posture; the paper is already local, so there is nothing to buy.
```

Build-because-posture (and because the paper is already local).

### Verdict

| Leg | Best candidate | Verdict |
|---|---|---|
| Anthropic-first | (none) | build-because-none |
| Marketplace | `jwynia/agent-skills@fact-check` (536 installs) | reference-only (general agent fact-checker; wrong shape + scope) |
| Own skills/tools | `dataroom-ingest` projection + `papered_source` schema + ingest-enrich pattern | **adapt-own (enabling work already done)** |
| Full-service | hosted fact-check/verification APIs | build-because-posture (confidentiality; paper already local) |

**Conclusion:** build the thin verifier — the projection + link already exist; the differentiator the
ecosystem doesn't cover is a *headless, source-anchored, advisory-only Paper-Governs* verdict wired into
our ingest leg, never an auto-ship signal.
