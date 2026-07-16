import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brief_threads as T  # noqa: E402


# --- parse_frontmatter ---

Acme = """---
id: acme-loan-extension
item: "Send the Greenfield / Sam Acme loan extension for signature"
conflict_key: familyoffice/wiki/entities/example-loans-llc.md
domain: familyoffice
status: parked
next_action: "DocuSigned; awaiting Sam's signature. ON SIGNED RETURN -> resolve OI-978/OI-997."
---

## History
- body
"""


def test_parse_frontmatter_extracts_fields():
    fm = T.parse_frontmatter(Acme)
    assert fm["id"] == "acme-loan-extension"
    assert fm["status"] == "parked"
    assert fm["conflict_key"] == "familyoffice/wiki/entities/example-loans-llc.md"
    assert "awaiting Sam" in fm["next_action"]


def test_parse_frontmatter_no_frontmatter_returns_empty():
    assert T.parse_frontmatter("no frontmatter here") == {}


def test_parse_frontmatter_strips_trailing_inline_comment():
    # A82: an unquoted value with a trailing ` # ...` comment must parse to the value alone,
    # not the whole literal string (which would fail every downstream vocabulary match).
    fm = T.parse_frontmatter(
        "---\nid: x\nstatus: open            # open | parked | resolved | reverted\n---\nbody"
    )
    assert fm["status"] == "open"


def test_parse_frontmatter_keeps_hash_inside_quoted_value():
    # a legitimate '#' inside a quoted scalar is NOT a comment and must survive
    fm = T.parse_frontmatter('---\nnext_action: "reply to notice #3 by Friday"\n---\nbody')
    assert fm["next_action"] == "reply to notice #3 by Friday"


def test_parse_frontmatter_does_not_strip_hash_from_join_fields():
    # A82 review MEDIUM #1: comment-stripping is scoped to `status` ONLY. An unquoted free-text
    # field (item/next_action) that carries a ` #` before an OI-id must keep it, or thread_oids
    # drops the OI and the item resurfaces COLD — the exact regression this tool exists to prevent.
    fm = T.parse_frontmatter(
        "---\nid: x\nitem: audit #ref then resolve OI-9\n"
        "next_action: chase paperwork #tracking then resolve OI-42\n---\nbody"
    )
    assert fm["next_action"] == "chase paperwork #tracking then resolve OI-42"
    assert fm["item"] == "audit #ref then resolve OI-9"
    assert {"OI-9", "OI-42"} <= T.thread_oids(fm)


def test_parse_frontmatter_quoted_status_with_trailing_comment():
    # A82 review MEDIUM #2: a quoted status carrying a trailing comment must parse to the bare
    # token (quotes AND comment removed) so court() classifies it, not to the literal '"parked"'.
    fm = T.parse_frontmatter('---\nid: x\nstatus: "parked"   # waiting on Sam\n---\nbody')
    assert fm["status"] == "parked"
    assert T.court(fm["status"]) == "others"


# --- thread_oids: OI-ids referenced anywhere in the thread ---

def test_thread_oids_from_next_action():
    thread = T.parse_frontmatter(Acme)
    assert T.thread_oids(thread) == {"OI-978", "OI-997"}


def test_thread_oids_from_id():
    thread = {"id": "OI-1000", "conflict_key": "familyoffice/wiki/tasks/OI-1000",
              "item": "", "next_action": ""}
    assert "OI-1000" in T.thread_oids(thread)


def test_thread_oids_case_insensitive_and_empty():
    assert T.thread_oids({"id": "", "item": "see OI-905 please", "next_action": "",
                          "conflict_key": ""}) == {"OI-905"}
    assert T.thread_oids({"id": "slug", "item": "", "next_action": "", "conflict_key": ""}) == set()


# --- court classification (three buckets: you / others / done) ---

def test_court_open_is_you():
    assert T.court("open") == "you"


def test_court_parked_is_others():
    assert T.court("parked") == "others"


def test_court_resolved_reverted_are_done():
    assert T.court("resolved") == "done"
    assert T.court("reverted") == "done"
    assert T.court("closed") == "done"


def test_court_unrecognized_status_degrades_to_you():
    # A82: a parse miss / garbage status must degrade toward VISIBLE (Act), never silently to
    # "others" (the ⏳ waiting track, which by design carries no A/B buttons). Blank counts too.
    assert T.court("") == "you"
    assert T.court(None) == "you"
    assert T.court("open            # open | parked | resolved") == "you"
    assert T.court("banana") == "you"


def test_unquoted_parked_with_template_comment_routes_to_others():
    # A82: the exact form the shipped template ships — an UNQUOTED parked status carrying the
    # ` # open | parked | ...` annotation — must parse to the bare token AND route to 'others'
    # (waiting track), pinned end-to-end so a strip that handled `open` but not `parked` can't slip.
    fm = T.parse_frontmatter(
        "---\nid: x\nstatus: parked            # open | parked | resolved | reverted\n---\nbody"
    )
    assert fm["status"] == "parked"
    assert T.court(fm["status"]) == "others"


def test_court_comment_polluted_status_parses_and_routes_to_you():
    # end-to-end: a thread whose status carries a trailing comment parses to "open" -> court "you".
    fm = T.parse_frontmatter(
        "---\nid: bridge-hedge\nstatus: open            # open | parked | resolved | reverted\n---\nx"
    )
    assert T.court(fm["status"]) == "you"


# --- link_item ---

def _threads():
    return [
        {"id": "OI-1000", "conflict_key": "familyoffice/wiki/tasks/OI-1000",
         "item": "IRS NOTICE-A", "status": "open",
         "next_action": "pull IRS transcript", "updated_utc": "2026-07-07T00:00:00Z"},
        # an OI-N thread that only CROSS-REFERENCES OI-1000 and OI-937 — it must NOT capture them
        {"id": "OI-1002", "conflict_key": "familyoffice/wiki/tasks/OI-1002",
         "item": "hedge", "status": "parked",
         "next_action": "compare with OI-1000; supersedes OI-937", "updated_utc": "2026-07-02T00:00:00Z"},
        {"id": "acme-loan-extension",
         "conflict_key": "familyoffice/wiki/entities/example-loans-llc.md",
         "item": "Acme extension", "status": "parked",
         "next_action": "awaiting signature; resolve OI-978/OI-997",
         "updated_utc": "2026-07-11T08:59:00Z"},
        {"id": "OI-1027", "conflict_key": "familyoffice/wiki/entities/m-d-properties-group-1-llc.md",
         "item": "Metropolis tax", "status": "open",
         "next_action": "send Meridian receipt; 2025 half into refi (OI-1016)",
         "updated_utc": "2026-07-07T00:00:00Z"},
        {"id": "OI-1032", "conflict_key": "familyoffice/wiki/tasks/OI-1032",
         "item": "STRC", "status": "resolved",
         "next_action": "done", "updated_utc": "2026-07-07T00:00:00Z"},
    ]


def test_link_item_exact_id_wins_over_mention():
    # OI-1000 has its OWN thread AND is mentioned by the parked OI-1002 thread — must pick OI-1000.
    im = T.link_item({"id": "OI-1000"}, _threads())
    assert im["thread_id"] == "OI-1000"
    assert im["court"] == "you"
    assert im["next_action"] == "pull IRS transcript"


def test_link_item_oi_thread_does_not_capture_by_mere_mention():
    # OI-937 is ONLY cross-referenced inside the OI-N thread OI-1002 — an OI-N thread owns only its
    # own id, so OI-937 must NOT link (regression guard for the HIGH review finding).
    assert T.link_item({"id": "OI-937"}, _threads()) is None


def test_link_item_slug_thread_owns_its_referenced_oids():
    # a slug-id thread (no OI-N of its own) owns every OI it references
    im = T.link_item({"id": "OI-997"}, _threads())
    assert im["thread_id"] == "acme-loan-extension"
    assert im["court"] == "others"


def test_link_item_multiple_items_share_a_slug_thread():
    assert T.link_item({"id": "OI-978"}, _threads())["thread_id"] == "acme-loan-extension"
    assert T.link_item({"id": "OI-997"}, _threads())["thread_id"] == "acme-loan-extension"


def test_link_item_honors_existing_thread_id():
    # synthetic id with no OI-id/conflict_key match, but the GATHER set thread_id (its judgment)
    im = T.link_item({"id": "FO-DEMO1", "thread_id": "OI-1027"}, _threads())
    assert im["thread_id"] == "OI-1027"
    assert im["court"] == "you"


def test_link_item_by_conflict_key():
    im = T.link_item({"id": "X-1",
                      "conflict_key": "familyoffice/wiki/entities/m-d-properties-group-1-llc.md"},
                     _threads())
    assert im["thread_id"] == "OI-1027"


def test_link_item_resolved_thread_is_done():
    im = T.link_item({"id": "OI-1032"}, _threads())
    assert im["court"] == "done"


def test_link_item_no_match_returns_none():
    assert T.link_item({"id": "OI-1899"}, _threads()) is None
    assert T.link_item({"id": "FO-UNTRACKED"}, _threads()) is None


# --- annotate_cache ---

def _cache():
    return {
        "act": [
            {"id": "OI-1000", "title": "NOTICE-A", "domain": "familyoffice"},
            {"id": "OI-1899", "title": "Unlinked", "domain": "familyoffice"},
        ],
        "stations": {
            "familyoffice": [
                {"id": "OI-997", "title": "Acme", "domain": "familyoffice",
                 "claude_voice": {"text": "keep"}},
            ],
        },
    }


def test_annotate_cache_sets_in_motion_and_preserves_fields():
    cache = _cache()
    count = T.annotate_cache(cache, _threads())
    assert count == 2  # OI-1000 (act) + OI-997 (station)
    assert cache["act"][0]["in_motion"]["thread_id"] == "OI-1000"
    # unmatched item stays clean
    assert "in_motion" not in cache["act"][1]
    # station item linked + other fields preserved
    st = cache["stations"]["familyoffice"][0]
    assert st["in_motion"]["thread_id"] == "acme-loan-extension"
    assert st["claude_voice"] == {"text": "keep"}


def test_annotate_cache_does_not_persist_a_derived_thread_id():
    # the tool NEVER writes the scalar thread_id (only the gather authors it) — otherwise a
    # derived link would go sticky and survive a corrected join rule (review finding #2).
    cache = _cache()
    T.annotate_cache(cache, _threads())
    assert "thread_id" not in cache["act"][0]


def test_annotate_cache_is_idempotent():
    cache = _cache()
    first = T.annotate_cache(cache, _threads())
    second = T.annotate_cache(cache, _threads())
    assert first == second == 2
    assert cache["act"][0]["in_motion"]["thread_id"] == "OI-1000"


def test_annotate_cache_empty_threads_is_noop():
    cache = _cache()
    assert T.annotate_cache(cache, []) == 0
    assert "in_motion" not in cache["act"][0]
