#!/usr/bin/env python3
"""Unit tests for lane_policy.py — the deterministic half of the gate.

Covers the review_gate escalation control (SIMPLIFICATION-SPRINT U4): resolve_review_gate +
gate_to_lane, including the HARD SAFETY CLAMP that a sensitive (non-auto-ship-cleared) KB can
never collapse. Pytest-collectable: `python -m pytest tools/tests/test_lane_policy.py -k gate`.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # .../engine/tools
import lane_policy

# The engine's DEFAULT_AUTO_SHIP_KBS is EMPTY (safety default — Global Constraint: auto-ship is
# opt-in per domain at setup, read from profile/connectors.yaml `gate.auto_ship_kbs`). Tests below
# that want to exercise "a kb IS cleared for auto-ship" pass this explicit set — it reproduces one
# example caller's choice (e.g. a profile that opted dev+personal in), never the engine default.
CLEARED_DEV_PERSONAL = frozenset({"dev", "personal"})


# ── safety default: DEFAULT_AUTO_SHIP_KBS is EMPTY ─────────────────────────────────────────

def test_default_auto_ship_kbs_is_empty():
    # the Global Constraint, structurally: no domain is auto-ship-cleared unless a caller opts it in
    assert lane_policy.DEFAULT_AUTO_SHIP_KBS == frozenset()


def test_gate_default_full_for_dev_and_personal_with_no_explicit_widen():
    # with the empty default, dev/personal are sensitive (non-cleared) too -> default gate is 'full',
    # same as any other unrecognized kb. Only an explicit auto_ship_kbs widen changes this.
    assert lane_policy.resolve_review_gate("dev") == "full"
    assert lane_policy.resolve_review_gate("personal") == "full"


def test_ship_action_default_ships_nothing_without_explicit_auto_ship_kbs():
    # THE NEW CONTRACT: with no explicit auto_ship_kbs, an auto-ship-lane item HOLDS regardless of kb —
    # including dev/personal, which is no longer implicitly cleared. Nothing auto-ships until a caller
    # opts a domain in (profile-driven — see deploy/tasks/gate.md / skills/gate/SKILL.md).
    assert lane_policy.ship_action({"lane": "auto-ship", "kb": "dev"}) == "hold"
    assert lane_policy.ship_action({"lane": "auto-ship", "kb": "personal"}) == "hold"
    assert lane_policy.ship_action({"lane": "auto-ship", "kb": "familyoffice"}) == "hold"
    # same for a confirm item past its TTL — the kb backstop still applies with an empty default
    old = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2020-01-01T00:00:00"}
    assert lane_policy.ship_action(
        old, now_epoch=lane_policy._epoch("2026-01-01T00:00:00")) == "hold"


def test_scheduled_ship_action_default_ships_nothing_without_explicit_auto_ship_kbs():
    # the unattended variant inherits the same empty default — no economic content needed to hold it,
    # the kb backstop alone is enough.
    assert lane_policy.scheduled_ship_action({"lane": "auto-ship", "kb": "dev"}, True) == "hold"
    assert lane_policy.scheduled_ship_action({"lane": "auto-ship", "kb": "personal"}, True) == "hold"


def test_a96_proposal_never_auto_ships_even_on_a_cleared_kb_and_auto_ship_lane():
    # A96: a proposal is a proposed operational-Notion write — it ALWAYS holds for human approval,
    # independent of lane/kb. Even mis-laned to auto-ship on an explicitly-cleared kb, it holds.
    prop = {"kind": "proposal", "lane": "auto-ship", "kb": "dev"}
    assert lane_policy.ship_action(prop, auto_ship_kbs={"dev"}) == "hold"
    # a BLOCKing review still rejects a proposal (review verdict wins over the proposal hold)
    assert lane_policy.ship_action(prop, review_passed=False, auto_ship_kbs={"dev"}) == "reject"
    # a non-proposal on a cleared kb + auto-ship lane still ships (the guard is proposal-scoped)
    assert lane_policy.ship_action({"lane": "auto-ship", "kb": "dev"}, auto_ship_kbs={"dev"}) == "ship"


def test_ship_action_explicit_auto_ship_kbs_preserves_prior_behavior():
    # passing an explicit cleared set reproduces the pre-fix {"dev","personal"} default's ship outcomes —
    # explicit-argument behavior is bit-identical to before this fix.
    assert lane_policy.ship_action(
        {"lane": "auto-ship", "kb": "dev"}, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    assert lane_policy.ship_action(
        {"lane": "auto-ship", "kb": "personal"}, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    # familyoffice still never ships, even with an explicit set that excludes it (Paper-Governs backstop)
    assert lane_policy.ship_action(
        {"lane": "auto-ship", "kb": "familyoffice"}, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


# ── gate resolution ──────────────────────────────────────────────────────────────────────

def test_gate_collapsed_for_kb_in_explicit_cleared_set():
    # a caller-supplied auto_ship_kbs (e.g. a profile that opted dev+personal in) -> 'collapsed'
    assert lane_policy.resolve_review_gate("dev", auto_ship_kbs=CLEARED_DEV_PERSONAL) == "collapsed"
    assert lane_policy.resolve_review_gate("personal", auto_ship_kbs=CLEARED_DEV_PERSONAL) == "collapsed"


def test_gate_default_full_for_sensitive_kb():
    # familyoffice is NOT cleared -> sensitive -> defaults to 'full'
    assert lane_policy.resolve_review_gate("familyoffice") == "full"


def test_gate_profile_value_honored_for_cleared_kb():
    gates = {"personal": "collapsed", "dev": "full"}
    assert lane_policy.resolve_review_gate(
        "personal", profile_gates=gates, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "collapsed"
    # a cleared KB MAY be tightened to full by the profile
    assert lane_policy.resolve_review_gate(
        "dev", profile_gates=gates, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "full"


def test_gate_sensitive_kb_clamped_even_if_profile_says_collapsed():
    # the safety clamp: familyoffice can NEVER be collapsed, even if the profile mis-sets it
    gates = {"familyoffice": "collapsed"}
    assert lane_policy.resolve_review_gate("familyoffice", profile_gates=gates) == "full"


def test_gate_sensitive_kb_clamped_even_via_category_override():
    # a per-category override also cannot collapse a sensitive KB
    overrides = {"btc-treasury": "collapsed"}
    assert lane_policy.resolve_review_gate(
        "familyoffice", category="btc-treasury", category_overrides=overrides) == "full"


def test_gate_category_override_takes_precedence_for_cleared_kb():
    # for a cleared KB, a per-category override beats the per-KB setting
    gates = {"dev": "collapsed"}
    overrides = {"esc": "full"}        # tighten one dev category to full
    assert lane_policy.resolve_review_gate(
        "dev", profile_gates=gates, category="esc", category_overrides=overrides,
        auto_ship_kbs=CLEARED_DEV_PERSONAL) == "full"
    # a dev category with no override falls back to the per-KB 'collapsed'
    assert lane_policy.resolve_review_gate(
        "dev", profile_gates=gates, category="aios", category_overrides=overrides,
        auto_ship_kbs=CLEARED_DEV_PERSONAL) == "collapsed"


def test_gate_unknown_value_falls_back_to_safe_default():
    # a typo'd gate value is ignored -> engine default (collapsed for cleared, full for sensitive)
    assert lane_policy.resolve_review_gate(
        "dev", profile_gates={"dev": "lite"}, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "collapsed"
    assert lane_policy.resolve_review_gate("familyoffice", profile_gates={"familyoffice": "lite"}) == "full"


def test_gate_clamp_respects_caller_widened_auto_ship_set():
    # if a caller clears familyoffice for auto-ship (their choice), the clamp no longer forces full
    cleared = frozenset({"dev", "personal", "familyoffice"})
    assert lane_policy.resolve_review_gate(
        "familyoffice", profile_gates={"familyoffice": "collapsed"}, auto_ship_kbs=cleared) == "collapsed"


# ── gate -> lane ─────────────────────────────────────────────────────────────────────────

def test_gate_full_forces_review_lane():
    assert lane_policy.gate_to_lane("full", "auto-ship") == "review"
    assert lane_policy.gate_to_lane("full", "confirm") == "review"
    assert lane_policy.gate_to_lane("full", "review") == "review"


def test_gate_collapsed_keeps_proposed_lane():
    assert lane_policy.gate_to_lane("collapsed", "auto-ship") == "auto-ship"
    assert lane_policy.gate_to_lane("collapsed", "confirm") == "confirm"
    assert lane_policy.gate_to_lane("collapsed", "review") == "review"


def test_gate_unrecognized_value_fails_safe_to_review():
    # only an explicit 'collapsed' preserves the proposed lane; any garbage gate escalates to review
    assert lane_policy.gate_to_lane("lite", "auto-ship") == "review"
    assert lane_policy.gate_to_lane(None, "auto-ship") == "review"
    assert lane_policy.gate_to_lane("", "auto-ship") == "review"


# ── end-to-end: the dry-run the sprint names (personal collapsed path, FO full review path) ──

def test_gate_personal_item_takes_collapsed_path_fo_takes_full():
    # profile opts personal+dev into auto-ship (an explicit caller choice, not the engine default)
    profile_gates = {"familyoffice": "full", "personal": "collapsed", "dev": "collapsed"}
    # a personal auto-ship draft keeps auto-ship (collapsed) -> ship_action ships it
    p_gate = lane_policy.resolve_review_gate(
        "personal", profile_gates=profile_gates, auto_ship_kbs=CLEARED_DEV_PERSONAL)
    p_lane = lane_policy.gate_to_lane(p_gate, "auto-ship")
    assert (p_gate, p_lane) == ("collapsed", "auto-ship")
    assert lane_policy.ship_action(
        {"lane": p_lane, "kb": "personal"}, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    # an FO auto-ship draft is forced to review (full) -> ship_action holds it for a human
    fo_gate = lane_policy.resolve_review_gate(
        "familyoffice", profile_gates=profile_gates, auto_ship_kbs=CLEARED_DEV_PERSONAL)
    fo_lane = lane_policy.gate_to_lane(fo_gate, "auto-ship")
    assert (fo_gate, fo_lane) == ("full", "review")
    assert lane_policy.ship_action(
        {"lane": fo_lane, "kb": "familyoffice"}, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


# ── economic tripwire + scheduled (unattended) auto-ship — ON4 (2026-06-26) ──
# The scheduled variant ships dev/personal auto-ship lanes with NO human present. ship_action trusts
# the kb label; an economic item MIS-laned into kb=dev would slip through. economic_tripwire is the
# deterministic floor that holds it. The fresh-context agent review still runs and still gates (reject).

def test_tripwire_fires_on_economic_phrases():
    assert lane_policy.economic_tripwire({"title": "Updated cap table for the SPV"})
    assert lane_policy.economic_tripwire({"text": "signed a promissory note with Bayview"})
    assert lane_policy.economic_tripwire({"tags": ["equity-stake"]})              # hyphenated slug
    assert lane_policy.economic_tripwire({"summary": "records an executed agreement"})
    assert lane_policy.economic_tripwire({"text": "ownership-transfer of the units"})


def test_tripwire_matches_plurals_and_stems():
    # regression guard for the trailing-\b bug: plurals + verb stems MUST still fire
    for s in ("two cap tables", "several promissory notes", "operating agreements",
              "refinance the Bayview loan", "refinancing in progress", "the mortgage", "into escrow",
              "quarterly dividend", "a lien on title", "the deed", "current valuation"):
        assert lane_policy.economic_tripwire({"text": s}), s


def test_tripwire_money_transfer_amounts():
    assert lane_policy.economic_tripwire({"text": "wired $2.5M to the seller"})
    assert lane_policy.economic_tripwire({"text": "transfer 500k at closing"})
    # a transfer verb with a NON-money count must NOT trip (avoid dev false-positives)
    assert not lane_policy.economic_tripwire({"text": "wired 3 endpoints to the gateway"})
    assert not lane_policy.economic_tripwire({"text": "data transfer between services"})


def test_tripwire_scans_real_queue_item_fields():
    # a real `awaiting` item has NO title/body — signal lives in the subject slugs + the enriched draft
    assert lane_policy.economic_tripwire({"draft_excerpt": "promissory note terms for Bayview"})
    assert lane_policy.economic_tripwire({"conflict_key": "dev/wiki/companies/cap-table-2026.md"})
    assert lane_policy.economic_tripwire({"id": "bayview-mortgage-refi-quote"})
    assert lane_policy.economic_tripwire({"payload_path": "raw/inbox/gmail/2026-escrow-wire.md"})


def test_tripwire_ignores_sorter_rec_reason_narrative():
    # 2026-06-27 production false-positive: the sorter's rec_reason narrative NAMES discipline categories
    # (almost always negated) for a CLEAN item, so scanning it manufactured false holds. rec_reason is the
    # sorter's justification, not the artifact's content — it must NOT trip the content classifier.
    assert not lane_policy.economic_tripwire(
        {"rec_reason": "clean dev daily note; no Paper-Governs material, auto-ship"})
    assert not lane_policy.economic_tripwire(
        {"rec_reason": "no security/account/payment content; no economic or ownership claims"})
    # the genuinely-economic case is unaffected: a real economic item is laned to `review` by the sorter
    # (its signal lives in the body, which the content fields still scan), so the floor loses no recall.
    # end-to-end: a clean dev auto-ship whose rec_reason merely mentions a discipline term now SHIPS
    # unattended (was the 06-25 daily-note false hold) instead of being deferred to the manual pass.
    it = {"lane": "auto-ship", "kb": "dev", "draft_excerpt": "Refactored the queue sharding helper.",
          "rec_reason": "no Paper-Governs material; clean low-risk dev capture, auto-ship"}
    assert lane_policy.scheduled_ship_action(
        it, review_passed=True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"


def test_tripwire_tags_as_string_scanned():
    # M1: a comma-joined string tags value is scanned, not silently skipped
    assert lane_policy.economic_tripwire({"tags": "bayview,promissory-note,fo"})
    assert not lane_policy.economic_tripwire({"tags": "dev,aios,pipeline"})


def test_tripwire_quiet_on_benign_dev_content():
    # recall-biased but ordinary dev/personal notes must NOT trip it (false-positive = needless hold)
    for s in ("Refactor the queue sharding helper", "fix the pytest collection in conftest",
              "evaluation of the new model", "wireframe for the landing page", "download the dataset"):
        assert not lane_policy.economic_tripwire({"text": s}), s
    assert not lane_policy.economic_tripwire({})


def test_scheduled_ships_clean_dev_and_personal_auto_ship():
    # happy path: a genuine dev/personal auto-ship with no economic smell ships unattended, PROVIDED
    # the caller's profile has opted the kb into auto_ship_kbs (never the engine default — see the
    # safety-default tests above) AND the draft body was enriched (HOLE A: an unenriched item can't be
    # screened, so it defers — the real scheduled skill enriches draft_excerpt before this call).
    assert lane_policy.scheduled_ship_action(
        {"lane": "auto-ship", "kb": "dev", "title": "anthropic model id note",
         "draft_excerpt": "claude-opus-4-8 is the current opus id; nothing economic here"}, True,
        auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    assert lane_policy.scheduled_ship_action(
        {"lane": "auto-ship", "kb": "personal", "title": "gym log", "text": "squat 3x5, no money content"}, True,
        auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"


def test_scheduled_holds_economic_item_mislabeled_into_dev():
    # THE ON4 invariant: economic content mis-laned into an auto-ship-cleared KB still HOLDS unattended
    it = {"lane": "auto-ship", "kb": "dev", "title": "cap table update", "text": "new equity stake"}
    # label-only would ship… (kb explicitly cleared, the way a real profile would clear it)
    assert lane_policy.ship_action(it, review_passed=True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    # …the tripwire holds it regardless
    assert lane_policy.scheduled_ship_action(
        it, review_passed=True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


def test_scheduled_holds_familyoffice_kb_backstop():
    # the kb backstop is unchanged under the scheduled path (correctly-labeled FO still holds)
    assert lane_policy.scheduled_ship_action({"lane": "auto-ship", "kb": "familyoffice"}, True) == "hold"


def test_scheduled_holds_review_lane():
    assert lane_policy.scheduled_ship_action({"lane": "review", "kb": "dev"}, True) == "hold"


def test_scheduled_rejects_on_failed_review():
    # bullet 3: the fresh-context review still gates the unattended ship — a BLOCK -> reject, even clean dev
    assert lane_policy.scheduled_ship_action(
        {"lane": "auto-ship", "kb": "dev"}, review_passed=False) == "reject"


def test_scheduled_confirm_ttl_with_tripwire_floor():
    # confirm within TTL holds; past TTL + clean ships; past TTL + economic holds (tripwire floor)
    now = lane_policy._epoch("2026-06-26T12:00:00")
    # all enriched with a body (HOLE A) so the ship/hold verdict turns on the TTL + tripwire, not
    # body-presence: young holds on TTL, old ships (clean body), old_econ holds on the tripwire.
    young = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-26T00:00:00",
             "draft_excerpt": "a clean dev note"}
    old = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-01T00:00:00",
           "draft_excerpt": "a clean dev note, nothing economic"}
    old_econ = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-01T00:00:00",
                "title": "notes", "draft_excerpt": "promissory note terms and the mortgage balance"}
    assert lane_policy.scheduled_ship_action(
        young, True, now_epoch=now, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"
    assert lane_policy.scheduled_ship_action(
        old, True, now_epoch=now, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    assert lane_policy.scheduled_ship_action(
        old_econ, True, now_epoch=now, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


# ── A48 HOLE A: the unattended tripwire needs a body to screen; a blind tripwire defers ──────

def test_scheduled_holds_auto_ship_item_with_no_enriched_body():
    # An auto-ship item in a CLEARED kb, review passed, that carries only subject/identity fields
    # (no draft_excerpt/body/text) — the enrichment was skipped/failed. The tripwire would scan blind,
    # so scheduled_ship_action must DEFER rather than ship into that blind spot.
    bare = {"lane": "auto-ship", "kb": "dev", "id": "some-note-2026", "conflict_key": "dev/wiki/knowledge/some-note.md"}
    assert lane_policy.scheduled_ship_action(bare, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


def test_scheduled_ships_auto_ship_item_once_body_is_enriched():
    # Regression: a clean, ENRICHED auto-ship item in a cleared kb still ships — the body-presence
    # floor must not break the legitimate unattended path (the scheduled skill enriches draft_excerpt).
    enriched = {"lane": "auto-ship", "kb": "dev", "id": "some-note-2026",
                "draft_excerpt": "a plain dev note about a refactor, nothing economic"}
    assert lane_policy.scheduled_ship_action(enriched, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"


def test_scheduled_body_presence_floor_does_not_touch_non_ship_actions():
    # a review-lane item (holds) or a failed review (reject) is never UPGRADED by the body check;
    # the floor only ever downgrades a would-be ship.
    assert lane_policy.scheduled_ship_action(
        {"lane": "review", "kb": "dev", "id": "x"}, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"
    assert lane_policy.scheduled_ship_action(
        {"lane": "auto-ship", "kb": "dev", "id": "x"}, False, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "reject"


def test_scheduled_holds_item_with_null_body_field():
    # A failed enrichment may record the miss as a NULL body (draft_excerpt: None), not an absent key.
    # str(None) is the truthy 'None', so a naive presence check would read a body and ship BLIND —
    # exactly the case HOLE A guards. The isinstance-str coercion must treat a null body as no body.
    null_body = {"lane": "auto-ship", "kb": "dev", "id": "note", "draft_excerpt": None}
    assert lane_policy.scheduled_ship_action(null_body, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"
    assert lane_policy._has_scannable_body({"draft_excerpt": None, "body": None}) is False
    assert bool(lane_policy._has_scannable_body({"draft_excerpt": "real text"})) is True


def test_scheduled_body_floor_applies_on_the_confirm_lane_too():
    # HOLE A is not auto-ship-only: a past-TTL confirm item with no enriched body is deferred, never
    # shipped over a blind tripwire. (A regression scoping the body check to auto-ship would miss this.)
    now = lane_policy._epoch("2026-06-26T12:00:00")
    bare_confirm = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-01T00:00:00"}  # past TTL, no body
    assert lane_policy.scheduled_ship_action(
        bare_confirm, True, now_epoch=now, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


# ── A48 HOLE C: the MANUAL gate gets a deterministic economic floor too (advisory) ───────────

def test_manual_holds_economic_item_mislabeled_into_cleared_kb():
    # THE HOLE C FIX: an economic-vocab item mis-laned to auto-ship in a CLEARED kb, run through the
    # MANUAL gate, must surface for explicit approval — before this, only scheduled applied the tripwire
    # and the manual gate shipped it on review-pass with no economic floor at all.
    econ = {"lane": "auto-ship", "kb": "dev", "id": "cap-table-2026",
            "draft_excerpt": "updated the cap table and the promissory note balance"}
    assert lane_policy.manual_ship_action(econ, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"
    # the economic SUBJECT identity is caught even with NO body enrichment (slug alone trips it)
    slug_only = {"lane": "auto-ship", "kb": "dev", "id": "promissory-note-roe",
                 "conflict_key": "dev/wiki/knowledge/promissory-note-roe.md"}
    assert lane_policy.manual_ship_action(slug_only, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


def test_manual_ships_clean_item_and_imposes_no_body_presence_floor():
    # manual differs from scheduled: a human is present, so a clean auto-ship item ships even WITHOUT
    # an enriched body (the manual flow may decide the lane before ship.py resolve attaches the excerpt).
    clean_bare = {"lane": "auto-ship", "kb": "dev", "id": "some-note-2026"}
    assert lane_policy.manual_ship_action(clean_bare, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"
    # and the kb backstop still holds in manual (a non-cleared kb never auto-ships)
    assert lane_policy.manual_ship_action(
        {"lane": "auto-ship", "kb": "familyoffice", "id": "x"}, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


def test_manual_holds_economic_confirm_past_ttl():
    # HOLE C on the confirm lane (the docstring's claim, now pinned): a past-TTL confirm item in a
    # cleared kb would ship, but an economic body surfaces it for explicit approval; a clean one ships.
    now = lane_policy._epoch("2026-06-26T12:00:00")
    econ_confirm = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-01T00:00:00",
                    "draft_excerpt": "the promissory note balance and the escrow account"}
    assert lane_policy.manual_ship_action(
        econ_confirm, True, now_epoch=now, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"
    clean_confirm = {"lane": "confirm", "kb": "dev", "first_drafted_utc": "2026-06-01T00:00:00"}
    assert lane_policy.manual_ship_action(
        clean_confirm, True, now_epoch=now, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "ship"


def test_manual_never_upgrades_a_reject_or_hold_to_ship():
    # the manual tripwire wrapper must only ever DOWNGRADE a would-be ship — never upgrade a non-ship base.
    assert lane_policy.manual_ship_action(
        {"lane": "auto-ship", "kb": "dev", "id": "x"}, False, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "reject"
    assert lane_policy.manual_ship_action(
        {"lane": "review", "kb": "dev", "id": "x"}, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"
    # an economic review-lane item stays hold — the tripwire branch never flips a non-ship base to ship
    assert lane_policy.manual_ship_action(
        {"lane": "review", "kb": "dev", "id": "cap-table-2026"}, True, auto_ship_kbs=CLEARED_DEV_PERSONAL) == "hold"


if __name__ == "__main__":
    # also runnable without pytest, matching the repo's other test files
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok  " + fn.__name__)
        except Exception:
            failed += 1; print(" FAIL " + fn.__name__); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
