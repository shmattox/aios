"""A109 task 1 — backlog item parser + station mapping."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backlog_parse import parse_backlog, station_for, BacklogIdError  # noqa: E402

SAMPLE = """# BACKLOG
## Now
- [ ] **A201** — build the thing ▶ started
  - acceptance: it works (shown)
- [ ] **A202** — decide the shape [GATE: human]
- [ ] **A203** — ✋[stuck] blocked on plan doc
- [ ] **A204** — plain queued item
## Next — seeds
- ◷ **A205 — future idea, headline only.** More prose.
## Done
- [x] **A200** — old thing ✅ 2026-07-21 (auto: closed)
- [x] **A199** — ancient ✅ 2026-01-01
"""


def test_parse_finds_all_items():
    items = {i["id"]: i for i in parse_backlog(SAMPLE)}
    assert set(items) == {"A199", "A200", "A201", "A202", "A203", "A204", "A205"}
    assert items["A205"]["state"] == "seed"
    assert items["A200"]["state"] == "done"
    assert items["A200"]["closed_date"] == "2026-07-21"
    assert items["A202"]["gate_human"] is True
    assert items["A201"]["headline"].startswith("build the thing")


def test_station_mapping():
    items = {i["id"]: i for i in parse_backlog(SAMPLE)}
    today = "2026-07-22"
    assert station_for(items["A205"], {}, today=today) == "incoming"      # seed
    assert station_for(items["A202"], {}, today=today) == "needs_you"     # gate
    assert station_for(items["A203"], {}, today=today) == "needs_you"     # stuck marker
    assert station_for(items["A201"], {}, today=today) == "in_motion"     # ▶
    assert station_for(items["A204"], {}, today=today) == "incoming"      # plain open = queued
    assert station_for(items["A200"], {}, today=today) == "shipped"       # closed within 2 days
    assert station_for(items["A199"], {}, today=today) is None            # old done → omit
    # standup group overrides an otherwise-quiet open item
    assert station_for(items["A204"], {"A204": "needs-you"}, today=today) == "needs_you"
    assert station_for(items["A204"], {"A204": "handed-off"}, today=today) == "in_motion"


# --- A144: a letter-suffixed id is REFUSED, never silently truncated -------------------------

def test_suffixed_id_raises_instead_of_truncating():
    """`PS392b` used to parse as id `PS392` with `b` as its headline. That is not cosmetic: a `b`
    item is normally split off a FINISHED parent, so the stem lands in the done set, the drain
    selector drops it, and genuinely open work goes permanently invisible. Refuse it instead."""
    try:
        parse_backlog("- [ ] **A26b** - the remainder of A26")
    except BacklogIdError as e:
        assert "A26b" in str(e)
        assert "letter suffix" in str(e)
    else:
        raise AssertionError("a suffixed id must raise, not parse")


def test_suffixed_id_is_refused_in_every_item_state():
    """open / done / seed all go through the same gate — a done suffixed id still poisons the
    done set that the open ones are checked against."""
    for line in ("- [ ] **A26a** - open", "- [x] **A26a** - done", "- ◷ **A26a** - seed"):
        try:
            parse_backlog(line)
        except BacklogIdError:
            pass
        else:
            raise AssertionError(f"must raise for: {line}")


def test_plain_ids_are_untouched():
    """The contract is letters+digits; everything already conforming must parse exactly as before."""
    items = parse_backlog(
        "\n".join(["- [ ] **A26** — fine", "- [x] **PS392** — also fine", "- [ ] **H144** — fine"])
    )
    assert [i["id"] for i in items] == ["A26", "PS392", "H144"]
    assert items[0]["headline"] == "fine"


def test_a_trailing_letter_elsewhere_in_the_line_is_not_an_id_suffix():
    """Only the id token itself is policed — prose after the id may contain anything."""
    items = parse_backlog("- [ ] **A26** - ship v2b of the thing")
    assert [i["id"] for i in items] == ["A26"]
