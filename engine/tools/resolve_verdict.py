#!/usr/bin/env python3
"""resolve_verdict.py — the DETERMINISTIC verdict gate of the task resolution layer.

The model assembles + semantically types evidence; it NEVER declares a figure "papered" by fiat.
It hands this tool a claim (the quantity under question) plus typed evidence rows, and THIS tool
applies the deference ladder + the strict clean rule to produce the verdict
(`papered`/`conflict`/`verbal-only`/`silent` + canonical cite + conflict reason + provenance) — so
it is auditable and cannot misfire on a model's whim. (Paper-Governs as code — see
docs/superpowers/specs/2026-07-07-task-resolution-layer-design.md.) Fact-free, stdlib-only.

Deference ladder (highest first): paper > operational > verbal.

The verdict is ADVISORY: it is rendered as a dossier card in the brief so Seth decides faster, but
every economic promotion still holds for his approval. (A former auto-promote boolean was retired
2026-07-10 — the resolve-fate decision: it was consumed by nothing, and wiring it to auto-promote a
papered economic figure without approval would loosen Paper-Governs. See
docs/superpowers/specs/2026-07-10-resolve-fate-decision.md.)
"""
import argparse, json, sys

TIER_RANK = {"paper": 3, "operational": 2, "verbal": 1}
MONEY_EPS = 0.01


def _eq(a, b):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < MONEY_EPS


def compute_verdict(claim_qty, evidence, paper_sources=frozenset({"drive"})):
    """(claim_qty, evidence[]) -> verdict dict (see the plan's shared contract)."""
    evidence = evidence or []
    if not claim_qty:
        return {"verdict": "silent", "canonical": None, "conflict": None,
                "provenance": []}

    # Ambiguity guard: any value-bearing candidate the model could not type blocks a clean verdict.
    untyped = [r for r in evidence if r.get("value") is not None and not r.get("qty")]

    # Aligned set: rows about the SAME quantity as the claim. Different-qty rows are NOT conflicts.
    aligned = [r for r in evidence if r.get("qty") == claim_qty]
    provenance = [r.get("source") for r in sorted(
        aligned, key=lambda r: TIER_RANK.get(r.get("tier"), 0), reverse=True)]

    if not aligned:
        return {"verdict": "silent", "canonical": None, "conflict": None,
                "provenance": provenance}

    # A paper-tier row governs ONLY if its source is a real paper-tier source (default: drive).
    # This cross-check makes the gate reject a mislabeled verbal/trello row claiming tier="paper".
    papers = [r for r in aligned if r.get("tier") == "paper" and r.get("executed")
              and r.get("source") in paper_sources]

    # The strict clean rule requires EXACTLY ONE executed governing doc. Two or more candidate
    # papers -> conflict regardless of whether their values agree (which doc governs is ambiguous).
    if len(papers) > 1:
        vals = sorted({str(r.get("says")) for r in papers})
        return {"verdict": "conflict", "canonical": None,
                "conflict": "multiple executed docs for this claim: %s" % vals,
                "provenance": provenance}

    if papers:
        gov = papers[0]
        gv = gov.get("value")
        if gv is None:
            return {"verdict": "conflict", "canonical": None,
                    "conflict": "governing doc figure unread (no value extracted)",
                    "provenance": provenance}
        contradictions = [r for r in aligned
                          if r is not gov and r.get("value") is not None and not _eq(r.get("value"), gv)]
        canonical = "%s — cited to %s:%s" % (gov.get("says"), gov.get("source"), gov.get("ref"))
        if contradictions or untyped:
            why = []
            if contradictions:
                c = contradictions[0]
                why.append("%s says %s; paper says %s" % (c.get("source"), c.get("says"), gov.get("says")))
            if untyped:
                why.append("an untyped figure is present — alignment unconfirmed")
            return {"verdict": "conflict", "canonical": canonical, "conflict": "; ".join(why),
                    "provenance": provenance}
        # STRICT CLEAN: exactly one executed paper, qty-aligned, nothing contradicts, nothing untyped.
        return {"verdict": "papered", "canonical": canonical, "conflict": None,
                "provenance": provenance}

    # No paper in the aligned set -> operational/verbal only; never auto-promotes.
    return {"verdict": "verbal-only", "canonical": None, "conflict": None,
            "provenance": provenance}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic resolve verdict gate")
    ap.add_argument("payload", help="path to JSON: {claim_qty, evidence:[...]}")
    args = ap.parse_args(argv)
    with open(args.payload, encoding="utf-8") as f:
        data = json.load(f)
    # A40: distinguish absent/None (default to {drive}) from an EXPLICIT empty list ("trust nothing").
    # `... or ["drive"]` collapsed both, so `paper_sources: []` silently reverted to {drive} and a
    # would-be no-paper-tier run still reached a `papered` verdict on a drive doc.
    raw_ps = data.get("paper_sources")
    ps = frozenset({"drive"}) if raw_ps is None else frozenset(raw_ps)
    print(json.dumps(compute_verdict(data.get("claim_qty"), data.get("evidence"), ps), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
