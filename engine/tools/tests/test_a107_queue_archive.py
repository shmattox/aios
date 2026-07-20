# sanitize:allow-file — fixtures use synthetic ids/values by design (A79)
"""A107 — queue archival: page terminal items out of the live queue.json, never reopen a rejection.

Covers the acceptance: archival leaves only non-terminal + in-window items (validate passes); a
rejected-then-archived proposal STILL dedupes (proposal_dedupe_history + reconcile.already_proposed +
the add id-fence); rewind undo-ship on an archived shipped item fails loud with the recover path;
unarchive restores. The window basis is the item's last-history `ts`.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import queue_tx  # noqa: E402
import brief_session  # noqa: E402
import reconcile_state_knowledge as recon  # noqa: E402
import rewind  # noqa: E402

NOW = "2026-07-20T00:00:00Z"
OLD = "2026-05-01T00:00:00Z"   # ~80d before NOW → past a 30d window
FRESH = "2026-07-15T00:00:00Z"  # ~5d before NOW → in-window


def _item(cid, stage, ts, **extra):
    it = {"id": cid, "stage": stage, "history": [{"ts": ts, "stage": stage}]}
    if stage in queue_tx.KEYED_STAGES:
        it["conflict_key"] = f"dev/wiki/knowledge/{cid}.md"
    it.update(extra)
    return it


def _write_queue(tmp_path, items):
    q = tmp_path / "queue.json"
    q.write_text(json.dumps({"queue": items}), encoding="utf-8")
    return str(q)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ─────────────────────────── archival core ───────────────────────────

def test_archive_moves_only_terminal_past_window(tmp_path):
    items = [
        _item("old-ship", "shipped", OLD),        # terminal + old → archived
        _item("old-reject", "rejected", OLD),     # terminal + old → archived
        _item("fresh-ship", "shipped", FRESH),    # terminal but in-window → stays
        _item("await", "awaiting", OLD, draft_path="dev/wiki/knowledge/await.md"),  # non-terminal → stays
    ]
    q = _write_queue(tmp_path, items)
    moved = queue_tx.archive(q, window_days=30, now=NOW)
    assert sorted(moved) == ["old-reject", "old-ship"]
    live_ids = {it["id"] for it in _load(q)["queue"]}
    assert live_ids == {"fresh-ship", "await"}
    arch = _load(queue_tx.archive_path_for(q))
    assert {it["id"] for it in arch["queue"]} == {"old-ship", "old-reject"}
    assert queue_tx.validate(_load(q)) is None
    assert queue_tx.validate(arch) is None


def test_archive_keeps_item_with_no_parseable_ts(tmp_path):
    it = {"id": "no-hist", "stage": "shipped", "conflict_key": "dev/wiki/knowledge/no-hist.md"}
    q = _write_queue(tmp_path, [it])
    assert queue_tx.archive(q, window_days=30, now=NOW) == []   # never archived blind
    assert {i["id"] for i in _load(q)["queue"]} == {"no-hist"}


def test_archive_noop_when_nothing_eligible(tmp_path):
    q = _write_queue(tmp_path, [_item("fresh", "shipped", FRESH)])
    assert queue_tx.archive(q, window_days=30, now=NOW) == []
    assert not os.path.exists(queue_tx.archive_path_for(q))  # no empty archive written


def test_archive_merges_into_existing_archive_and_dedupes(tmp_path):
    # a pre-existing archive (with 'p') + a new archival run of 'q' → archive holds both, no dup
    q = _write_queue(tmp_path, [_item("q", "shipped", OLD)])
    ap = queue_tx.archive_path_for(q)
    Path(ap).write_text(json.dumps({"queue": [_item("p", "rejected", OLD)]}), encoding="utf-8")
    queue_tx.archive(q, window_days=30, now=NOW)
    ids = [it["id"] for it in _load(ap)["queue"]]
    assert sorted(ids) == ["p", "q"] and len(ids) == len(set(ids))  # merged, no duplicate


def test_archive_keeps_item_with_malformed_ts(tmp_path):
    # a non-None but unparseable ts → _epoch()==0.0 → kept live (never archived blind)
    it = _item("bad-ts", "shipped", "not-a-date")
    q = _write_queue(tmp_path, [it])
    assert queue_tx.archive(q, window_days=30, now=NOW) == []
    assert {i["id"] for i in _load(q)["queue"]} == {"bad-ts"}


def test_unarchive_cleans_a_ghost_when_already_live(tmp_path):
    # simulate a crash-duplicated item present in BOTH live and archive; unarchive must clean the
    # archive ghost even though the id is already live (crash-idempotency).
    q = _write_queue(tmp_path, [_item("dup", "shipped", OLD)])
    Path(queue_tx.archive_path_for(q)).write_text(
        json.dumps({"queue": [_item("dup", "shipped", OLD)]}), encoding="utf-8")
    queue_tx.unarchive(q, ["dup"])
    assert [i["id"] for i in _load(q)["queue"]] == ["dup"]              # still one live copy
    assert _load(queue_tx.archive_path_for(q))["queue"] == []          # ghost cleaned from archive


# ─────────────────────────── the rejection-memory invariant ───────────────────────────

def test_archived_rejected_proposal_still_dedupes(tmp_path):
    key = "dev/notion/tasks/foo|2026-07-01"
    rej = _item("rej-prop", "rejected", OLD, kind="proposal", payload={"dedupe_key": key},
                conflict_key="dev/notion/tasks/foo")
    q = _write_queue(tmp_path, [rej])
    # before archival: remembered in the live queue
    assert len(brief_session.proposal_dedupe_history(q, key)) == 1
    queue_tx.archive(q, window_days=30, now=NOW)
    assert {i["id"] for i in _load(q)["queue"]} == set()          # queue emptied
    # after archival: STILL remembered (from the archive) — the door stays closed
    assert len(brief_session.proposal_dedupe_history(q, key)) == 1


def test_archived_reconcile_proposal_still_dedupes(tmp_path):
    key = "dev/wiki/knowledge/p.md|dev/assets/x|100.00"
    it = _item("recon-rej", "rejected", OLD, reconcile={"dedupe_key": key})
    q = _write_queue(tmp_path, [it])
    assert recon.already_proposed(q, key) is True
    queue_tx.archive(q, window_days=30, now=NOW)
    assert recon.already_proposed(q, key) is True                # still deduped from the archive


def test_add_refuses_an_archived_id(tmp_path):
    q = _write_queue(tmp_path, [_item("gone", "shipped", OLD)])
    queue_tx.archive(q, window_days=30, now=NOW)                  # 'gone' now lives only in the archive
    assert _load(q)["queue"] == []
    with pytest.raises(SystemExit):                              # re-adding the archived id is refused
        queue_tx._apply_items(q, [_item("gone", "captured", NOW)], "add")


# ─────────────────────────── revert / recover path ───────────────────────────

def test_unarchive_round_trip(tmp_path):
    q = _write_queue(tmp_path, [_item("x", "shipped", OLD)])
    queue_tx.archive(q, window_days=30, now=NOW)
    assert _load(q)["queue"] == []
    assert queue_tx.unarchive(q, ["x"]) == ["x"]
    assert {i["id"] for i in _load(q)["queue"]} == {"x"}
    assert _load(queue_tx.archive_path_for(q))["queue"] == []    # removed from the archive


def test_undo_ship_on_archived_id_fails_loud_with_recover_path(tmp_path, capsys):
    q = _write_queue(tmp_path, [_item("shipped-1", "shipped", OLD)])
    queue_tx.archive(q, window_days=30, now=NOW)
    with pytest.raises(SystemExit):
        rewind.undo_ship(q, "shipped-1", str(tmp_path / "vault"), str(tmp_path / "revert"))
    out = capsys.readouterr().out
    assert "ARCHIVED" in out and "unarchive" in out             # documented recover path shown


def test_undo_ship_missing_id_still_plain_not_found(tmp_path, capsys):
    q = _write_queue(tmp_path, [_item("real", "shipped", FRESH)])
    with pytest.raises(SystemExit):
        rewind.undo_ship(q, "ghost", str(tmp_path / "vault"), str(tmp_path / "revert"))
    assert "id not found" in capsys.readouterr().out
