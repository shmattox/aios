# Pipeline Queue — shared contract (shipped)

The single coordination structure the whole capture → keep-fresh pipeline hangs off. Every stage
reads and writes it; it is what makes **parallel workers safe** and what makes **"you just review"** real. Every stage that touches it obeys the **Stage Contract** (`STAGE-CONTRACT.md`).

- **Contract** (this file) = engine, identical for everyone.
- **Live state** (the actual items) = per-install, a **single file** at `<install>/state/queue.json`
  — **never inside the plugin tree** (operational state stays home — separation map).

## State layout — single file (A4, 2026-07-05)

The live queue is **one canonical JSON file** at `<install>/state/queue.json` = `{"queue": [...]}`.
All writes go through `queue_tx.py` (`add` / `update` / `claim` / `commit`), which writes
atomically (tmp file + `os.replace`) and serializes writers with a short-lived advisory lock,
`queue.json.lock`. Durable history and rollback come from **git** (`state/` is tracked) plus
**rewind snapshots** — there is no derived mirror or backup copy.

- Stages never hand-edit the file: they read a subset (`queue_tx.py select --stage …`) and write
  only the items they change (`add` new / `update` existing). These per-item ops are **targeted**
  (touch only the named items, never prune), so two stages working different items can't
  collateral-delete each other — the contract's concurrency promise.
- Bulk/operator ops (rewind, garden full-rewrites) use `queue_tx.py commit` — an
  authoritative WHOLE-queue replace that DOES prune items not in the proposed set. Correct for "this
  is the entire queue now," but **not concurrency-safe**; the daily pipeline stages never use it.
- The G13 sharded queue layout (`queue.json.d/`) was retired in A4; the one-time `migrate`
  collapse verb is gone (removed as dead code, A51). A lingering legacy shard dir now fences
  every op fail-loud instead — collapse one via a pre-A4 tag that still ships `migrate`, then
  upgrade back.

## Item schema — one entry per unit of work

```jsonc
{
  "id": "bayview-csl-bridge-quote",         // stable, unique; used in commands ("revert {id}")
  "source": "x|bookmark|email|whatsapp|wiki|self",
  "kb": "familyoffice|personal|dev",       // routed target (set by Sort)
  "stage": "captured|sorted|awaiting|shipped|reverted|rejected",  // the whole lifecycle, one field
  "lane": "auto-ship|confirm|review",      // risk lane (confirm opt-in; see Lanes)
  "conflict_key": "familyoffice/wiki/companies/bayview-flats-llc.md",  // kb-prefixed wiki target it touches
  "claimed_by": null,                      // worker lease — null = free to claim
  "claimed_at": null,                      // ISO; lease expires after TTL → dead worker frees item
  "recommended": "approve|hold|reject",    // the pipeline's pre-decided ballot
  "rec_reason": "one line",
  "payload_path": "raw/inbox/gmail/2026-06-12-csl-bridge....md",  // the raw/draft this refers to
  "captured_utc": "2026-06-12T...",        // when capture enqueued it (provenance only — NOT a clock)
  "first_drafted_utc": "2026-06-20T...",   // the one clock — confirm-timeout measures from this
  "history": []                            // append-only stage transitions (audit + undo trail)
}
```

## Lifecycle (the four-phase lifecycle, per item)

`captured → sorted → awaiting → shipped`  (+ terminal `rejected` / `reverted`)

Lanes (`auto-ship` / `confirm` / `review`) decide *how* `awaiting → shipped` happens — they are
**not** stages. An item reaches `shipped` **only** through the gate (auto-ship lane,
or human approval on the review lane). No stage skips the gate.

## Claim / lease — what makes fan-out safe

- A worker **claims** an item by setting `claimed_by` + `claimed_at` in one atomic write; only an
  item with `claimed_by: null` is claimable.
- A claim is a **lease with a TTL** (default 15 min). If a worker dies, the lease expires and the
  item frees itself — no manual unsticking.
- **One writer per item, always.** This is the direct fix for the 2026-06-11 whole-file clobber
  (two parallel Phase B sessions overwrote `phase-a-pending.json`): workers mutate their *claimed
  item*, never blind-write the whole file.

## Conflict-keys — serialize work on shared targets

- Two items with the **same `conflict_key`** (same wiki page / same fact) must **not** be processed
  in parallel — the second waits until the first ships. The review gate is the serialization point.
- Items with **different** conflict-keys fan out freely across N workers. That's where the
  parallelism (multiple build/review agents) lives — safely.
- **`conflict_key` is canonical** for routing & serialization; `kb` / `domain` are conveniences
  derived from it. If they ever disagree, `conflict_key` wins.

## Lanes — risk (attention scales with risk, not uniformly)

- **`auto-ship`** — low-risk, reversible (dev / personal-learning) → ships unattended; shown
  post-hoc in the review panel ("⚡ auto-shipped — review / revert").
- **`confirm`** *(opt-in per profile — `pipeline.confirm_lane`)* — default-approves after a timeout
  (profile TTL, e.g. 3 days from `first_drafted_utc`) if you don't act (personal-schedule-type
  items). A soft gate, not a hard hold. A minimal install ships only `auto-ship` + `review`.
- **`review`** — Paper-Governs / economic / ownership / FO / personal-ops / irreversible / ≥3-unit /
  conflict-flagged, **and every `source:self` skill-edit** → HOLDS for human approval, always.
  Escalation to `review` overrides any KB-default lane.
- **`review_gate` (profile-driven escalation, per KB-domain).** The profile (`profile/domains.yaml`
  → `review_gates`) sets each KB to `full` or `collapsed`. `full` = every item in that KB escalates to
  the `review` lane (the profile-driven form of the override above); `collapsed` = items keep their
  risk-based lane. Ingest finalizes the lane through `lane_policy.gate_to_lane(resolve_review_gate(kb,…),
  proposed_lane)` — tested code, not prose. **Safety clamp:** a *sensitive* KB (one not cleared for
  auto-ship — `familyoffice` by default) can never resolve to `collapsed`, so Paper-Governs material
  cannot be collapsed by accident even if the profile mis-sets it. New-install default: `collapsed`
  for cleared KBs, `full` for sensitive ones.
- **Daily-note collision (session-record clobber guard).** A `type: session-record` draft whose
  target `{kb}/wiki/journal/<date>.md` **already exists with substantive content from another producer**
  (e.g. a richer pre-pipeline daily note) escalates to `review` — a human confirms the merge. And at
  ship time the gate **merges, never overwrites** a daily note (see gate guardrails), so even
  a mis-laned session-record can never destroy an existing note. Only when the target is absent (the pipeline
  is the sole producer for that date) may a session-record stay `auto-ship`.

## Atomic writes

- Every write via `queue_tx.py`: build the new queue in memory → write it to a tmp file →
  validate → `os.replace` into place, under the `queue.json.lock` advisory lock. A crash mid-write
  leaves the old file intact — never a torn queue (fixes the NUL-pad / torn-write history).
- Reads are plain reads — no retries, no recovery layer. A file that fails to parse is real
  on-disk damage: restore from git (`state/` is tracked) or a rewind snapshot, then re-run.
