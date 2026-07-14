#!/usr/bin/env python3
"""lane_policy.py - the DETERMINISTIC half of the gate, defined once as tested code.

gate is mostly agent judgment (the independent fresh-context review of a draft vs its source).
But two pieces of it are pure, deterministic rules that must NOT be re-implemented per caller (the way
they were — as an `if lane == ...` branch copied into every script, untestable and driftable):

  1. lane -> action      : the gate's core safety invariant. auto-ship ships; **review ALWAYS holds
                           for a human** (never auto-shipped); confirm ships only once past its TTL.
  2. lane <-> ballot     : the consistency rule a real bug established (BACKLOG 2026-06-20 — a `hold`
                           ballot can't sit on an auto-ship lane).
  3. kb -> may-auto-ship : a KB not cleared for unattended promotion is ALWAYS human-gated — an
                           auto-ship (or past-TTL confirm) lane never ships it; it holds for a human.
                           The cleared set is policy the caller passes (profile-driven — read from
                           `<env_root>/profile/connectors.yaml` key `gate.auto_ship_kbs` — and the
                           eventual pattern-learning layer feeds it). SAFETY DEFAULT (Global Constraint):
                           with NO explicit auto_ship_kbs the default is EMPTY — nothing auto-ships,
                           including dev/personal; auto-ship is opt-in per domain at setup. Any single
                           project's install (e.g. an install that opts dev+personal in via
                           its profile) is a CALLER choice, never the engine default. familyoffice is
                           never in ANY caller's default either, so Paper-Governs material never
                           auto-ships unless a caller explicitly widens the set. (Added 2026-06-22 with
                           the SHIP widen to personal+FO — closes the lane-vs-kb gap flagged then:
                           ship_action was lane-only, so a familyoffice/auto-ship item would have
                           shipped. Tightened to an empty default 2026-07-01 — the source project's
                           {dev, personal} default leaked into the generic plugin; safety default now
                           ships EMPTY everywhere, opt-in per domain at setup.)

Codified here so the gate's invariants are TESTED CODE, not prose duplicated across the test, the
driver, and the skill. `skills/gate` references this for the mechanical decision; the agent
supplies only the PASS/BLOCK judgment that feeds `review_passed`. Fact-free, stdlib-only.
"""
import re
import time

CONFIRM_TTL_DAYS = 3   # default; a profile may override via pipeline.confirm_ttl_days

# KBs cleared for unattended promotion. Any kb NOT here is always human-gated (never auto-ships),
# even on an auto-ship lane. SAFETY DEFAULT: EMPTY — with no explicit auto_ship_kbs argument, NOTHING
# auto-ships. A caller (gate, profile-driven — `<env_root>/profile/connectors.yaml` key
# `gate.auto_ship_kbs`, written by the setup skill) opts specific domains in per install; the engine
# never assumes any domain (including dev/personal) is safe to auto-ship. familyoffice stays excluded
# from every caller's default set too — Paper-Governs never auto-ships unless explicitly widened.
DEFAULT_AUTO_SHIP_KBS = frozenset()


def _epoch(iso):
    """UTC ISO stamp -> epoch. timegm, NOT mktime — first_drafted_utc is written with gmtime,
    and mktime would parse it as local time, skewing the confirm-TTL by the timezone offset."""
    try:
        import calendar
        return float(calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S")))
    except Exception:
        return 0.0


def ship_action(item, review_passed=True, now_epoch=None, confirm_ttl_days=CONFIRM_TTL_DAYS,
                auto_ship_kbs=DEFAULT_AUTO_SHIP_KBS):
    """Map (lane, kb, independent-review verdict, clock) -> the action gate must take.

      'reject' : independent review BLOCKed it (critical finding) — terminal, regardless of lane/kb.
      'ship'   : promote the draft into the vault now (auto-ship lane, or confirm past its TTL) AND
                 the item's kb is cleared for auto-ship.
      'hold'   : keep awaiting for human approval (review lane always; confirm within its TTL; OR a kb
                 not cleared for auto-ship, regardless of lane — the Paper-Governs backstop).

    The PASS/BLOCK is the agent's judgment (caller passes it as review_passed); everything else here
    is deterministic. This is the single source of two invariants: 'never auto-ship a review-lane item'
    AND 'never auto-ship a KB outside auto_ship_kbs' (familyoffice by default — Paper-Governs).
    """
    if not review_passed:
        return "reject"
    lane = item.get("lane")
    # kb backstop: a KB not cleared for unattended promotion is ALWAYS human-gated. An auto-ship or
    # past-TTL confirm decision is downgraded to 'hold' for it. (review/unknown lanes already hold.)
    kb_cleared = item.get("kb") in auto_ship_kbs
    if lane == "auto-ship":
        return "ship" if kb_cleared else "hold"
    if lane == "review":
        return "hold"                      # NEVER auto-ship a review-lane item
    if lane == "confirm":
        now = time.time() if now_epoch is None else now_epoch
        age_days = (now - _epoch(item.get("first_drafted_utc"))) / 86400.0
        if age_days >= confirm_ttl_days:
            return "ship" if kb_cleared else "hold"
        return "hold"
    return "hold"                          # unknown/missing lane -> safest action is to hold


# ── economic tripwire — a best-effort floor under the UNATTENDED scheduled ship (ON4, 2026-06-26) ──
# WHAT IT IS / ISN'T. The PRIMARY guarantees against shipping Paper-Governs material unattended are
# (1) the kb backstop in ship_action (a correctly-labeled familyoffice item NEVER auto-ships) and
# (2) the fresh-context independent review (review_passed=False -> reject). This tripwire is a THIRD,
# best-effort layer for the residual case those two miss: an economic/ownership item MIS-laned into an
# auto-ship-cleared KB (kb=dev/personal, lane=auto-ship, review passed). A regex over text can never be
# a complete economic classifier — so it is deliberately RECALL-biased (over-inclusive): a false positive
# only DEFERS the item to the next human-gated pass (fail-safe = hold), which is the right asymmetry for
# an unattended money gate. It does NOT make the path "safe against any mislabel"; content carrying no
# economic vocabulary is the acknowledged residual, caught only by Sort + the review. Extend the patterns
# as real misses surface. NOTE: NO trailing \b on the group — that would kill every plural/suffix
# ("cap tables", "refinance"); the leading \b alone prevents prefix false-positives ("evaluation").
ECONOMIC_TRIPWIRE_RE = re.compile(
    r"\b("
    # multi-word phrases (separator [ -] so hyphenated tag-slugs trip like prose)
    r"paper[ -]governs|cap[ -]table|promissory[ -]note|operating[ -]agreement|"
    r"partnership[ -]agreement|membership[ -]interest|distribution[ -]waterfall|"
    r"purchase[ -]price|wire[ -]transfer|transfer[ -]funds|"
    r"equity[ -](?:stake|grant|interest|holder|holding)|"
    r"ownership[ -](?:stake|interest|transfer)|"
    r"executed[ -](?:agreement|contract|deed|doc|document)|"
    r"capital[ -](?:call|account|gain)|"
    # single high-signal economic / ownership / legal-financial stems (no trailing \b -> match suffixes)
    r"promissor|refinanc|mortgage|escrow|lien|deed|dividend|valuation|beneficiary|"
    r"shareholding|annuity|loan|k-1\b|"
    # a transfer verb immediately followed by a money amount ("wired $2.5M", "transfer 500k")
    r"(?:wire|wired|transfer|transferred|remit|remitted|disburse|disbursed)"
    r"\s+(?:\$[\d,.]+|\d[\d,.]*\s?(?:k|m|mm|bn|million|billion|thousand|usd|dollars?)\b)"
    r")",
    re.IGNORECASE)

# Fields scanned: the item's CONTENT and SUBJECT identity — NOT the sorter's decision narrative.
#   subject/identity slugs : id, conflict_key, payload_path  (what the item is ABOUT — e.g. "cap-table-2026")
#   content                : title, summary, text, body, draft_excerpt, name  (the artifact itself; the
#                            scheduled skill ENRICHES draft_excerpt from staging since the body is not on
#                            the bare item) + tags (scanned separately in economic_tripwire).
# DELIBERATELY EXCLUDES rec_reason (2026-06-27 fix): rec_reason is the SORTER's justification for the lane
# it chose, not the artifact's content. A justification narrative names discipline categories by nature —
# usually in the NEGATIVE for a clean item ("no Paper-Governs material, auto-ship"; "no security/account/
# payment content"; "no economic or ownership claims") — and a regex cannot tell that negation from a real
# economic body. Scanning it made every clean dev/personal note whose rec_reason merely MENTIONED a
# discipline term trip the floor: the first unattended run (2026-06-27) held a whole 06-25 dev daily note
# (all 5 contributors) on "Paper-Governs" appearing in a negated rec_reason, shipping only 1 of 40 items.
# It is also useless for the tripwire's real job: a genuinely-economic item the sorter NOTICED is laned to
# `review` (never reaches this floor); the residual the floor exists for is one the sorter MIS-read — whose
# rec_reason therefore reads "clean", and whose only true signal is the draft body. So the content/subject
# fields catch real misses; rec_reason only manufactured false holds. (Method note: BACKLOG.md.)
_TRIPWIRE_FIELDS = ("id", "conflict_key", "payload_path",
                    "title", "summary", "text", "body", "draft_excerpt", "name", "note")

# The CONTENT-bearing subset of the above — the artifact body the tripwire actually needs to screen
# for economic vocabulary. The subject/identity fields (id/conflict_key/payload_path/title/name) are
# always present on a bare queue item but are NOT the body; if NONE of these content fields is
# populated, the scheduled skill's draft-excerpt enrichment (gate/SKILL.md) was skipped or failed, so
# the tripwire is scanning blind. HOLE A: an unattended ship over a blind tripwire is a silent gap —
# scheduled_ship_action turns it into a deterministic defer (below).
_TRIPWIRE_BODY_FIELDS = ("draft_excerpt", "body", "text", "summary", "note")


def _has_scannable_body(item):
    """True if the item carries any non-empty CONTENT field the tripwire can actually screen — i.e.
    the draft body was enriched onto the item. Subject/identity slugs don't count as a body.

    Only a real non-empty STRING counts: a body key present with value None (a plausible way a failed
    enrichment records the miss) must NOT read as a body — `str(None)` is the truthy 'None', which
    would silently defeat HOLE A for exactly the enrichment-failed case it guards. isinstance-str is the
    fail-safe: anything that isn't real text is treated as 'no body' → defer."""
    return any(isinstance(item.get(f), str) and item.get(f).strip() for f in _TRIPWIRE_BODY_FIELDS)


def economic_tripwire(item):
    """True if an item's text smells economic / ownership / Paper-Governs — the unattended-path floor.
    Scans the item's CONTENT + subject-identity fields (+ any draft excerpt the caller attached) + tags
    against ECONOMIC_TRIPWIRE_RE — deliberately NOT the sorter's rec_reason narrative, which names
    discipline categories (usually negated) and only manufactured false holds (see _TRIPWIRE_FIELDS).
    Used only by scheduled_ship_action (with a human present it's unnecessary); a hit means 'defer to a
    human'. Recall-biased by design — see ECONOMIC_TRIPWIRE_RE."""
    parts = [str(item.get(f, "")) for f in _TRIPWIRE_FIELDS]
    tags = item.get("tags")
    if isinstance(tags, (list, tuple)):
        parts.extend(str(t) for t in tags)
    elif tags:                                   # a string (or other) tags value — scan it too
        parts.append(str(tags))
    return bool(ECONOMIC_TRIPWIRE_RE.search(" ".join(parts)))


def _ship_with_economic_floor(item, review_passed, now_epoch, confirm_ttl_days, auto_ship_kbs,
                              require_body):
    """Shared core of the two tripwire-guarded gate variants: ship_action + the economic_tripwire
    floor, single-sourced so the two public wrappers can't drift (the exact copy-paste class this
    module's header warns against). Only ever DOWNGRADES a would-be `ship` to `hold`; a `reject`/
    `hold`/non-ship base is returned untouched. `require_body` adds HOLE A's body-presence guard
    (unattended only — see the wrappers)."""
    base = ship_action(item, review_passed=review_passed, now_epoch=now_epoch,
                       confirm_ttl_days=confirm_ttl_days, auto_ship_kbs=auto_ship_kbs)
    if base == "ship":
        if require_body and not _has_scannable_body(item):
            return "hold"      # HOLE A: unenriched body — the tripwire is blind, defer to a human
        if economic_tripwire(item):
            return "hold"      # mis-labeled economic content — defer to a human, never auto-ship
    return base


def scheduled_ship_action(item, review_passed=True, now_epoch=None,
                          confirm_ttl_days=CONFIRM_TTL_DAYS, auto_ship_kbs=DEFAULT_AUTO_SHIP_KBS):
    """The action for the UNATTENDED scheduled auto-ship variant (ON4). ship_action PLUS two floors,
    because NO human is present: (1) the economic_tripwire holds an item whose CONTENT smells economic/
    ownership/Paper-Governs even when its kb/lane label would auto-ship it (the mis-label the kb backstop
    can't see); (2) HOLE A body-presence — a would-be ship whose draft excerpt wasn't enriched (the
    tripwire would scan blind) is deferred, so a skipped/failed enrichment can't silently ship past the
    screen. All other invariants (review-lane / confirm-TTL / kb-backstop / BLOCK) stay single-sourced in
    ship_action. Use this — never bare ship_action — wherever the ship runs with NO human present. A
    `reject`/`hold` is never upgraded; only a would-be `ship` can be downgraded."""
    return _ship_with_economic_floor(item, review_passed, now_epoch, confirm_ttl_days, auto_ship_kbs,
                                     require_body=True)


def manual_ship_action(item, review_passed=True, now_epoch=None,
                       confirm_ttl_days=CONFIRM_TTL_DAYS, auto_ship_kbs=DEFAULT_AUTO_SHIP_KBS):
    """The action for the MANUAL gate (`aios-gate`, a human present). ship_action plus an ADVISORY
    economic floor (HOLE C, 2026-07-09): an auto-ship/past-TTL-confirm item whose text smells economic/
    ownership/Paper-Governs is surfaced for the human's EXPLICIT approval ('hold') instead of being
    bulk-auto-shipped — even in an auto-ship-cleared KB. Before this, ONLY the unattended path applied
    the tripwire, so a mis-laned economic item run through the manual gate shipped on review-pass with no
    deterministic economic floor at all.

    Unlike scheduled_ship_action it does NOT impose the body-presence floor (`require_body=False`): a
    human is present to notice a thin item, and the manual flow may decide the lane before enriching the
    draft excerpt — so it scans whatever is on the item (the economic SUBJECT identity — e.g. a
    `cap-table-2026` slug — is caught even without body enrichment). A `reject`/`hold` is never upgraded."""
    return _ship_with_economic_floor(item, review_passed, now_epoch, confirm_ttl_days, auto_ship_kbs,
                                     require_body=False)


def ballot_consistent(lane, recommended):
    """The lane<->ballot rule (BACKLOG 2026-06-20: a 'hold' ballot can't sit on an auto-ship lane).
    auto-ship/confirm default to `approve`; a `review`-lane item is held, so its ballot is hold/reject."""
    if lane == "auto-ship":
        return recommended == "approve"
    if lane == "confirm":
        return recommended == "approve"
    if lane == "review":
        return recommended in ("hold", "reject")
    return True


# ── review_gate — profile-driven escalation control (SIMPLIFICATION-SPRINT U4, 2026-06-25) ──
# A per-domain knob in the profile (<env_root>/profile/domains.yaml) that picks HOW MUCH review a KB gets:
#   'full'      -> every item escalates to the `review` lane (always holds for a human) — the
#                  profile-driven form of QUEUE.md's "escalation to review overrides any KB-default lane".
#   'collapsed' -> items keep their risk-based lane (auto-ship/confirm/review as the sorter/ingest judged).
# Fact-free: the engine supplies only the DEFAULTS + the safety clamp; the per-KB VALUES live in the
# profile. "Sensitive" is defined structurally as "a KB NOT cleared for auto-ship" (i.e. not in
# auto_ship_kbs — familyoffice by default), so no KB name is hardcoded as sensitive here.
REVIEW_GATES = ("full", "collapsed")


def resolve_review_gate(kb, profile_gates=None, category=None, category_overrides=None,
                        auto_ship_kbs=DEFAULT_AUTO_SHIP_KBS):
    """Resolve the effective review_gate ('full' | 'collapsed') for an item.

    Precedence: per-category override > per-KB profile setting > engine default.
    Engine default: 'collapsed' for an auto-ship-cleared KB, 'full' for a sensitive (non-cleared) KB.

    HARD SAFETY CLAMP: a sensitive KB (one NOT in auto_ship_kbs — familyoffice by default) can NEVER
    resolve to 'collapsed', even if the profile or a per-category override says so. This is the
    Paper-Governs backstop in gate form: FamilyOffice/economic material cannot be collapsed by accident.
    """
    gate = None
    if category_overrides and category in category_overrides:
        gate = category_overrides.get(category)
    elif profile_gates and kb in profile_gates:
        gate = profile_gates.get(kb)
    if gate not in REVIEW_GATES:                       # unset / typo'd -> safe default
        gate = "collapsed" if kb in auto_ship_kbs else "full"
    if kb not in auto_ship_kbs and gate == "collapsed":  # safety clamp — sensitive can't collapse
        gate = "full"
    return gate


def gate_to_lane(review_gate, proposed_lane):
    """Map (review_gate, the sorter/ingest's proposed lane) -> the lane to finalize.
    Only an explicit 'collapsed' gate leaves the proposed lane intact; 'full' AND any unrecognized
    value FAIL SAFE to the `review` lane (more human review, never less) — so a future second caller
    that passes a typo'd gate escalates rather than silently auto-ships. Pairs with `resolve_review_gate`;
    ingest calls this to finalize the lane (the deterministic half of the lane decision — tested)."""
    return proposed_lane if review_gate == "collapsed" else "review"
