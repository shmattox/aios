# Span-write concurrency model (A124)

> **Re-implemented on current `main` 2026-09-01.** The original attempt lived on the
> stranded `factory/A124` branch (124 commits behind, conflicting). This document's
> analysis was re-checked against current `main` before reuse — `activity.py` still mints
> `span_id` as `f"{run_id}#{len(spans) + 1}"` and `grep lock` over the module returned
> nothing, so the problem below was still live and unmitigated. The design is unchanged;
> the verification section records THIS implementation's evidence.

Constraint for the workflow-instrumentation plan's fan-out step (multiple concurrent
agent processes emitting `activity.py span-start`/`span-end` against ONE run record).

## Problem

`engine/tools/activity.py` mints `span_id` as `f"{run_id}#{len(spans)+1}"` under a
read→mutate→`_atomic_write` sequence. `_atomic_write` is atomic per-write (temp file +
`os.replace`) but not across the read-modify window, and the module had no cross-process
lock. With one writer per run this was harmless; with N concurrent agent processes each
racing `span-start`/`span-end` on the same record, two reads can observe the same
`len(spans)`, mint the same `span_id`, and the later `os.replace` clobbers the earlier
write — a dropped span, a duplicated `span_id`, and (via `end_span`) a lost cost update
that silently undercounts `run_cost`.

## Chosen model: per-run advisory file lock

A per-run `.lock` file (`{run_id}.json.lock`) guards the read-modify-write window of
every mutator that touches a run record (`heartbeat`, `start_span`, `end_span`,
`request_approval`, `resolve_approval`, `finish_run`). Locking is OS-level file locking —
`msvcrt.locking` on Windows, `fcntl.flock` on POSIX — because writers are separate OS
processes (the CLI is shelled out to per span by fan-out agents), not threads in one
process; an in-process lock (e.g. `threading.Lock`) would not serialize across processes.

Rationale for this over the alternatives named in the backlog item:

- **Single-writer aggregation** (the workflow harness owns the record; agents report up
  over some IPC) would need a long-lived coordinator process per run and a transport
  between agent and harness — new infrastructure this fan-out doesn't otherwise need.
  The CLI's whole design point is that each agent process is a disposable one-shot
  `activity.py` invocation; a lock is the least infrastructure that preserves that.
- **Append-only span log coalesced on read** avoids locking on write but pushes the
  read-modify-write hazard onto every reader (`read_all`, `build_graph`, the dashboard)
  and turns `span_id` minting into a separate reconciliation pass — a bigger surface
  change for the same guarantee.
- **Per-run file lock** keeps the existing read-modify-write shape, touches only the
  mutators, and is symmetric with the module's existing atomic-write pattern.

Retry policy is errno-discriminating, and this is load-bearing: only CONTENTION errnos
(`EACCES` from `msvcrt`, `EAGAIN`/`EWOULDBLOCK` from `flock`) are retried. Any other `OSError`
means the filesystem does not implement locking at all (`ENOLCK`/`EINVAL`/`EOPNOTSUPP` on SMB,
NFS and some FUSE mounts) — retrying that is dead time on EVERY mutator call forever, which
would stall a producer for the full deadline per call and break this module's core invariant.
The 2026-09-01 review caught this: with a blanket `except OSError`, three `heartbeat()` calls
on an unlockable filesystem took 15.07s. Pinned by
`test_unlockable_filesystem_does_not_stall_the_producer`.

Beyond that, a bounded non-blocking-lock retry loop (20ms poll, 5s deadline) rather than
a blocking OS lock call, so a wedged lock (e.g. a killed holder on a platform without
lock-release-on-exit guarantees) degrades to "proceed unlocked" instead of hanging a
producer indefinitely — consistent with the module's standing invariant that its own I/O
must never raise into or block a caller's real work. A lock file that never gets cleaned
up is removed by `prune()` alongside its terminal record.

Locking is scoped to the six mutators; `start_run` (record creation) and readers
(`read_all`, `build_graph`, `run_cost`) are unaffected — `start_run` is called once by
the one process that owns the id, and readers already tolerate a torn intermediate read
via `_read_json`'s try/except (a reader observes some consistent past write, never a
half-written file, because `_atomic_write` itself is atomic).

## Verification

`engine/tools/tests/test_activity.py`:
- `test_span_start_concurrent_processes_get_distinct_ids_zero_drops` — spawns 12 real
  `python activity.py span-start` subprocesses concurrently against one run record;
  asserts 12 distinct `span_id`s and zero dropped spans.
- `test_end_span_concurrent_processes_exact_run_cost` — spawns 10 real `span-end`
  subprocesses concurrently, each closing a distinct pre-opened span with a distinct
  cost; asserts every span closed (`status == "ok"`) and `run_cost` sums to the exact
  expected total.

- `test_prune_removes_the_lock_sidecar` — a `.lock` outlives its record otherwise, so
  `prune()` would leave one per pruned run accumulating in `state/activity/` forever.

Verified on this implementation (2026-09-01), not inherited from the earlier attempt:

- Both race tests were written FIRST and observed to FAIL against current `main`
  (`duplicate span_ids` / `an end_span update was lost`) — the race is real on the
  shipped code, not theoretical.
- After the lock: 33/33 in `test_activity.py`, and the two race tests pass **8/8**
  repeated runs (a concurrency fix that passes once has proved nothing).
- Non-vacuity: neutering `_run_lock` to a no-op yield makes both tests fail again, so
  the lock is demonstrably what carries them — they are not passing incidentally.
- Removing the errno filter makes `test_unlockable_filesystem_does_not_stall_the_producer`
  fail and the file take **15.30s** instead of 1.23s — that test earns its place.
- `test_start_span_racing_end_span_across_processes` covers the realistic fan-out shape
  (open and close concurrently); the other two each stress ONE mutator in isolation.
- `test_contended_lock_still_writes_after_the_deadline` pins the degraded branch, which
  is the most consequential path in this code and was previously inferred, not tested.

## NEW-2 (deferred)

`end_span` on an unknown `span_id` still silently no-ops. Left as-is: no real caller is
wired yet to observe a found/not-found signal, and the backlog item marks this minor —
revisit when the workflow-instrumentation CLI callers are actually wired.
