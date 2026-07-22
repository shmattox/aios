"""A109 task 1 — backlog item parser + station mapping."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backlog_parse import parse_backlog, station_for  # noqa: E402

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
