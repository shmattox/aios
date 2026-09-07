"""A6142 — the gate badge and the gate list must come from the same place.

The defect this locks out: the AIOS drill-in read `/api/held`, which the server served out of
`state/brief-cache.json` — a NIGHTLY artifact — while the badge beside it read live state. The
cache was empty, so the panel said "nothing in gate right now" against a live queue of 13. That
renders as a healthy queue, which is why nobody investigates it; an empty derived artifact
reporting as an empty world is a *confident* wrong answer.

So the assertion here is deliberately about AGREEMENT, not about any particular count. A test
that pinned "13" would pass just as happily with both numbers wrong together. Every test below
derives its expectation from the fixture it wrote.
"""
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard"))
from dashboard_server import make_server  # noqa: E402


def _env(tmp_path, n_gate, *, stale_cache=True):
    """A queue with `n_gate` items awaiting, and a brief cache that disagrees with it."""
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "connectors.yaml").write_text(
        "vault:\n  live_root: SecondBrain\n  live_kb_map:\n    personal: 01_Personal\n",
        encoding="utf-8")
    staging = tmp_path / "SecondBrain" / "01_Personal" / "wiki" / "staging"
    staging.mkdir(parents=True)
    queue = []
    for i in range(n_gate):
        (staging / f"d{i}.md").write_text(f"# draft {i}", encoding="utf-8")
        queue.append({
            "id": f"gate-{i}", "stage": "awaiting", "kb": "personal",
            "lane": "review" if i % 2 == 0 else "auto-ship",
            "recommended": "ship", "rec_reason": f"because {i}",
            "conflict_key": f"personal/wiki/sources/p{i}.md",
            "draft_path": f"01_Personal/wiki/staging/d{i}.md",
            "first_drafted_utc": f"2026-09-{i + 1:02d}T00:00:00Z",
        })
    queue.append({"id": "not-at-gate", "stage": "sorted", "kb": "personal"})
    (tmp_path / "state" / "factory").mkdir(parents=True)
    (tmp_path / "state" / "queue.json").write_text(json.dumps({"queue": queue}), encoding="utf-8")
    # the cache is EMPTY while the queue is not — the exact 2026-09-06 condition
    (tmp_path / "state" / "brief-cache.json").write_text(
        json.dumps({"held": [] if stale_cache else queue[:n_gate],
                    "generated_utc": "2026-09-06T10:55:00Z"}), encoding="utf-8")
    return tmp_path


def _serve(env_root):
    srv = make_server(env_root, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(srv, path):
    port = srv.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read())


@pytest.mark.parametrize("n_gate", [0, 1, 7])
def test_badge_and_list_agree_whatever_the_cache_says(tmp_path, n_gate):
    """The regression is the DISAGREEMENT, so all three readings are compared to each other."""
    srv = _serve(_env(tmp_path, n_gate))
    try:
        badge = next(n for n in _get(srv, "/api/content")["nodes"] if n["id"] == "gate")["count"]
        panel = _get(srv, "/api/content/stage/gate")
        held = _get(srv, "/api/held")["held"]
    finally:
        srv.shutdown()
    assert badge == n_gate, "the badge must read the queue the fixture wrote"
    assert panel["count"] == len(panel["items"]) == badge
    assert len(held) == badge, "/api/held must not answer from the nightly cache"
    assert {i["id"] for i in held} == {i["id"] for i in panel["items"]}


def test_an_empty_cache_does_not_empty_the_gate(tmp_path):
    """The 2026-09-06 failure, named: cache says nothing held, queue says otherwise."""
    env = _env(tmp_path, 3, stale_cache=True)
    assert json.loads((env / "state" / "brief-cache.json").read_text())["held"] == []
    srv = _serve(env)
    try:
        assert len(_get(srv, "/api/held")["held"]) == 3
    finally:
        srv.shutdown()


def test_gate_rows_carry_what_the_rich_card_needs(tmp_path):
    """Gate rows used to come from a richer source; folding them into `_row` must not strip it."""
    srv = _serve(_env(tmp_path, 2))
    try:
        row = _get(srv, "/api/content/stage/gate")["items"][0]
    finally:
        srv.shutdown()
    for k in ("id", "title", "kb", "lane", "recommended", "rec_reason",
              "conflict_key", "draft_path"):
        assert k in row, f"the Ship/Reject card reads {k}"


def test_draft_is_addressed_by_id_not_position(tmp_path):
    """A positional index over a LIVE list is a race: you would read one draft while approving
    another. Ordering here is newest-first, so `gate-0` is deliberately NOT row 0."""
    srv = _serve(_env(tmp_path, 3))
    try:
        items = _get(srv, "/api/content/stage/gate")["items"]
        assert items[0]["id"] != "gate-0", "fixture must not make position and id agree"
        for it in items:
            d = _get(srv, f"/api/draft?id={it['id']}")
            assert d["markdown"] == f"# draft {it['id'].split('-')[1]}", \
                "each id must resolve to ITS OWN draft, whatever row it sits on"
    finally:
        srv.shutdown()


def test_unknown_and_non_gate_ids_are_refused(tmp_path):
    """The id is an allowlist into a server-computed list — never a caller-supplied path."""
    srv = _serve(_env(tmp_path, 2))
    try:
        for bad in ("nope", "not-at-gate", "../secret"):
            with pytest.raises(urllib.error.HTTPError) as e:
                _get(srv, f"/api/draft?id={urllib.parse.quote(bad)}")
            assert e.value.code == 404, f"{bad!r} must not resolve"
    finally:
        srv.shutdown()
