# test_seed_slugmap.py — hermetic: build a fake mirror in tmp, seed it, assert map.
import json, tempfile, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import domain_sync as dsync


def _write(p, notion_id, title="X"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: state-thing\nnotion_id: {notion_id}\nlast_synced: 2026-01-01\n---\nbody\n", encoding="utf-8")


def test_seed_reads_notion_id_to_stem():
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        _write(sd/"tables"/"decisions"/"example-decision.md", "00000000000000000000000000000042")
        _write(sd/"tables"/"tasks"/"book-physical.md", "aaaa1111bbbb2222cccc3333dddd4444")
        got = dsync.seed_slugmap(sd)
        assert got == {
            "00000000000000000000000000000042": "example-decision",
            "aaaa1111bbbb2222cccc3333dddd4444": "book-physical",
        }, got
        # written file round-trips
        from stable_slugs import load_slugmap
        assert load_slugmap(sd/"_slugmap.json") == got


def test_seed_skips_record_without_notion_id():
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        (sd/"tables"/"notes").mkdir(parents=True)
        (sd/"tables"/"notes"/"no-id.md").write_text("---\ntype: x\n---\nbody\n", encoding="utf-8")
        got = dsync.seed_slugmap(sd)
        assert got == {}, got  # a record with no notion_id is skipped (counted), never guessed


def test_seed_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        _write(sd/"tables"/"notes"/"n.md", "id1")
        dsync.seed_slugmap(sd, dry_run=True)
        assert not (sd/"_slugmap.json").exists()


def test_seed_cli_refuses_gm_silo():
    # `sync_silo()` already refuses silo=="gm" (S9 — GM is local-SSOT, never a
    # Notion-derived mirror); `--seed --silo gm` must be refused the SAME way,
    # BEFORE any file is written, not just left to whatever load_silo_config/
    # seed_slugmap would happen to do with a gm state_dir.
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        gm_sd = root / "state" / "domains" / "gm"
        _write(gm_sd/"tables"/"notes"/"n.md", "id1")  # a record that WOULD seed if not refused
        raised = False
        try:
            dsync.main(["--seed", "--silo", "gm", "--env-root", str(root)])
        except SystemExit as exc:
            raised = True
            assert exc.code not in (0, None), exc.code  # argparse .error() -> non-zero exit
        assert raised, "expected --seed --silo gm to raise SystemExit (refused), it did not"
        assert not (gm_sd/"_slugmap.json").exists()


def test_seed_preserves_existing_entries():
    # NON-DESTRUCTIVE: seeding merges into an existing _slugmap.json — an entry
    # already present WINS (A84), and an id this .md-scan cannot see (e.g. an FO
    # .ndjson-row id) is preserved, never clobbered. Guards the economic-data
    # footgun of a full overwrite.
    from stable_slugs import save_slugmap, load_slugmap
    with tempfile.TemporaryDirectory() as d:
        sd = pathlib.Path(d)
        _write(sd/"tables"/"notes"/"n.md", "diskid1")            # on disk as stem "n"
        save_slugmap(sd/"_slugmap.json", {"unseenid": "kept-slug", "diskid1": "old-slug"})
        got = dsync.seed_slugmap(sd)
        assert got["unseenid"] == "kept-slug", got                # id not on disk is preserved
        assert got["diskid1"] == "old-slug", got                  # existing entry wins over disk stem "n"
        assert load_slugmap(sd/"_slugmap.json") == got


# ---- harness footer (exactly once, at end of file) ----
# GUARDED by __main__ (A6284). This file is a HYBRID: it defines module-level `def test_*`, so
# `conftest.py` deliberately does NOT ignore it (its rule is "ignore only what pytest cannot
# import"). Unguarded, this `sys.exit` fired during COLLECTION and aborted the entire run with
# `INTERNALERROR ... SystemExit: 0` — `python -m pytest -q` from the repo root reported
# "no tests ran", exit 3, collecting NOTHING repo-wide. That is the command registered as this
# repo's factory `test_cmd`, so the drain gate was verifying nothing at all.
#
# With the guard, both mechanisms work as conftest.py describes them: pytest imports the file and
# collects the five `def test_*` below, AND `python test_seed_slugmap.py` (how `suite_test.py`
# subprocesses it) still runs them and exits non-zero on failure. Complementary, not redundant.
if __name__ == "__main__":
    FAIL = []
    for _fn in (test_seed_reads_notion_id_to_stem,
                test_seed_skips_record_without_notion_id,
                test_seed_dry_run_writes_nothing,
                test_seed_cli_refuses_gm_silo,
                test_seed_preserves_existing_entries):
        try:
            _fn()
        except Exception as exc:  # noqa: BLE001 - collect every failure, don't stop at the first
            FAIL.append(f"{_fn.__name__}: {exc}")

    print("FAILURES:", FAIL)
    sys.exit(1 if FAIL else 0)
