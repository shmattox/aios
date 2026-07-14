# G14b — End-to-end pipeline test (the repeatable procedure)

The cutover gate (G14) needs the full chain `inbox-capture → sort → ingest → gate → brief → garden`
proven to **work together across every handoff**, ALL-IN-CODE (G14b) and ALL-IN-COWORK (G14c). This
file is the **all-in-Code** procedure. It has two halves — the same split the backlog names:

| Half | What it proves | How to run | Artifact |
|---|---|---|---|
| **Plumbing** (scriptable) | The state machine + deterministic policy the helpers enforce around every handoff: stages (incl. `rejected`), conflict-key serialization, vault files, revert pointers, reconcile-clean, context-log honesty, lane→action via `lane_policy`. | `python tools/tests/test_e2e_pipeline.py` | `test_e2e_pipeline.py` (hermetic, CI-able, 64 asserts) |
| **Judgment** (agent pass) | That **real agents** — given the skills + a raw — produce sort decisions, drafts, and review verdicts that flow through the **same** `queue_tx` mechanics the plumbing test exercises. | the agent pass below | this section |

Passing **both** is G14b green. (G14a static-coherence is already done; G14c is the same chain run as
the 5 registered Cowork tasks, owner-triggered.)

---

## Half 1 — Plumbing (deterministic, run anytime)

```
python tools/tests/test_e2e_pipeline.py     # exit 0 = all green
python tools/tests/test_queue_tx_g13.py     # the G13 shard-helper unit suite (no regression)
```

The plumbing test builds an **isolated temp install** (`tempfile.mkdtemp`, never a real install's live queue),
seeds 8 synthetic raws spanning every lane + a **conflict-key collision pair** + a **review-BLOCK
(reject) case**, and drives all five stages through the real helpers (`queue_tx`, `rewind`,
`garden_sweep`, `lane_policy`) with only the **independent-review PASS/BLOCK verdict canned**.
It asserts, at every handoff:

> **What is tested code vs. agent judgment.** The gate's *deterministic* decisions — lane→action
> (incl. the "never auto-ship a `review`-lane item" invariant), the confirm-TTL, and the lane↔ballot
> rule — live in `tools/lane_policy.py` and are exercised directly (not re-implemented in the test),
> so a regression there fails this suite. The *judgment* — whether a draft PASSes or is BLOCKed
> against its source — is the agent's job (Half 2); the plumbing test feeds it as a canned verdict.
> The only hand-rolled mechanic is the physical ship (copy staging draft → vault + write a revert
> pointer), because gate is a SKILL, not a code helper; that same step is shared by the driver.

- **Stages** advance `captured → sorted → awaiting → shipped` (+ terminal `rejected`) and nothing
  skips the gate. The BLOCK case reaches terminal `rejected` with no vault file (the gate doesn't leak).
- **Lanes** (`lane_policy`): auto-ship ships; review **holds**; confirm holds within TTL then **ships
  on timeout**; any BLOCK → reject. The lane↔ballot rule (no `hold` ballot on an auto-ship lane) is asserted.
- **Serialization**: two items sharing a `conflict_key` → only one claims/ships; the other waits;
  and they share **one** staging draft (the conflict_key is the single wiki target).
- **Vault files** exist for shipped items, **never** for held ones (the gate doesn't leak).
- **Revert pointers** (`state/revert/{id}.json` with `shipped_path`) written per ship.
- **reconcile-clean** end-to-end; and reconcile **detects + heals** a deleted vault file
  (loop-until-stable: `shipped → awaiting → sorted` when the draft is also gone).
- **Garden** sweeps stale residue + orphan drafts, **keeps** live drafts, and proposes a connect-hub
  through the gate (`awaiting`/`review`/`source:self`) — never silently ships it.
- **Brief** review-panel data = exactly the held review/confirm items (no shipped leakage).
- **Context-log honesty**: each stage's logged counts equal what actually landed on disk.

If a helper's contract changes, this test fails — it's the executable form of the Stage Contract.

---

## Half 2 — Judgment (the live agent pass)

Same chain, but the three **judgment** points are decided by fresh-context agents instead of canned:

1. **Sort + Ingest agent** — reads each raw + `skills/sort` + `skills/ingest`, returns per item:
   `{id, kb, conflict_key, lane, recommended, rec_reason, draft_markdown}`. The driver applies it
   through `queue_tx` (`add` → `update` to `sorted` → write the staging draft → `update` to `awaiting`).
2. **Independent review agent** — a SEPARATE fresh context (never the drafter grading its own work).
   Reads each draft + its source raw + `skills/gate` + `QUEUE.md §Lanes`, returns per item:
   `{id, verdict: PASS|BLOCK, reason}`. Checks: draft matches source; no Paper-Governs violation
   (no economic term promoted past `verbal` without an executed Drive doc); one-home-per-fact.
3. **Ship application** — the driver, not an agent: `auto-ship` + `PASS` → promote draft to vault +
   revert pointer + `shipped`; `review` lane → **hold** (surfaces in the brief) regardless of verdict;
   any `BLOCK` → `rejected` with the reason. Then assert final state + reconcile-clean.

### Fixture (isolated — never the live queue)

A temp install with 3 synthetic raws chosen to exercise all three ship outcomes:
- a **dev / auto-ship** raw (a reversible dev-tool note) → expected: drafted, PASS, **shipped**;
- an **FO / Paper-Governs** raw (an economic loan term) → expected: drafted, `review` lane, **held**;
- a **dev / security** raw (a security notice) → expected: drafted, `review` lane, **held**.

### Run it

The driver lives in `tools/tests/run_agent_pass.py` (sets up the fixture, applies the returned
decisions through the real helpers — `queue_tx` for state, `lane_policy` for the ship/hold/reject
decision — then asserts the final state). Run it live by spawning the two agents (Agent tool, fresh
context each) and feeding their JSON to the `apply-draft` / `apply-review` steps:

```
python tools/tests/run_agent_pass.py setup        <scratch>/_e2e_agentpass
#   -> spawn the Sort+Ingest agent on the printed raws; save its JSON
python tools/tests/run_agent_pass.py apply-draft   <scratch>/_e2e_agentpass draft.json
#   -> spawn the independent-review agent on the printed draft paths; save its JSON
python tools/tests/run_agent_pass.py apply-review  <scratch>/_e2e_agentpass review.json   # asserts + exits 0/1
```

**Replay (no agents):** the last green run's agent decisions are committed at
`tools/tests/fixtures/agent_pass_{draft,review}.json`, so the judgment half is reproducible from the
repo — `apply-draft`/`apply-review` against those files re-prove the chain without spawning agents.
The pass is **green** when: every raw drafted, the auto-ship item shipped with a vault file + revert
pointer, both review items held, any BLOCK rejected (no vault file, reason recorded), reconcile-clean.

### Last proven

- **2026-06-21** (all-in-Code) — plumbing **64/64**; live agent pass green (3 raws: dev-tool
  **shipped** after independent PASS; FO economic term + dev security notice both **held** on the
  `review` lane; agent correctly kept the FO term marked verbal/not-papered per Paper-Governs;
  reconcile-clean). Reviewed by an independent fresh-context gate (BLOCKED first pass → `lane_policy`
  extracted + reject/one-pass-reconcile/ballot cases added → SHIP). Decisions committed under
  `fixtures/`. See `BACKLOG.md` G14b.

---

## Why this is the gate (not ceremony)

G14a proved the pipeline is **statically** coherent (the handoffs line up on paper). G14b proves it is
**dynamically** coherent — the state actually moves correctly through every helper, and the judgment
layer plugs into that state machine without breaking it. Only then does pointing the pipeline at real
records (Phase 5 cutover) rest on something tested rather than assumed.
