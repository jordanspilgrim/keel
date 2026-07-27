"""Repair `conversations.offer_made` for offers left in the R12 `abandoned` state.

WHY THIS EXISTS, AND WHY IT IS NOT A RE-ROLL
--------------------------------------------
R12 added `abandoned` as a fourth terminal offer state (an offer presented and never
answered) but did not add it to `offers.extended()`, which decides what gets persisted into
the `offer_made` column. On the committed run that silently dropped the offer from 68 of 160
conversations -- EVERY ONE of them a loss -- so the offer-effectiveness panel counted the
offers that worked and discarded the ones that were ignored. Purely survivorship-directional.

The conversations themselves are correct and unchanged. Only a DERIVED column was wrong, and
the evidence needed to recompute it (the full offer ledger) is already stored in
`conversations.evidence_json` from the same run. This script recomputes that column from that
evidence and re-exports the dashboard. It does NOT re-simulate anything, does not call any
model, and cannot change which conversations exist or how they ended.

Three properties make this a data repair rather than metric rigging, and the script ASSERTS
all three rather than asking you to trust them:

  1. No `saved` row is touched, so the headline save-rate lift cannot move.
  2. No realized margin cost is added (an abandoned offer was never accepted, so it costs
     nothing), so the margin-adjusted lift cannot move either.
  3. The correction moves the offer-effectiveness numbers DOWN -- it enlarges the
     denominators with failures. If this made the product look better, that would be a
     reason to distrust it.

R14 corrections to this script's own honesty, all found by review:
  * It claimed to "re-export the dashboard". It did not -- the export was performed by hand.
    --apply now actually recomputes and rewrites every derived artifact.
  * It said "the script ASSERTS all three" safety properties. Only two were asserted; the
    unasserted one was the ANTI-RIGGING property (that the correction moves numbers DOWN).
    All three are now real asserts.
  * It repaired `conversations.offer_made` but not `disposition_json.offer_made`, which is
    the field the JUDGE reads -- leaving 68 rows in a shape production code cannot produce.
    Both are now written from the same ledger, and a fourth assert refuses to leave them
    disagreeing.

Run:  .venv/bin/python scripts/repair_abandoned_offer_made.py [--apply]
Without --apply it is a dry run and writes nothing.
"""

from __future__ import annotations

import json
import shutil
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402
import economics  # noqa: E402
import run_demo  # noqa: E402
from agent import offers as O  # noqa: E402
from analytics import themes  # noqa: E402
from dashboard import export  # noqa: E402


def _ledger_offer_summary(evidence_json: str) -> str | None:
    """Rebuild the ledger from persisted evidence and ask the (now fixed) extended()."""
    try:
        ev = json.loads(evidence_json)
    except Exception:
        return None
    ledger = [
        O.Offer(id=o.get("id", "x"), kind=o.get("kind"),
                authorized_terms=o.get("authorized_terms") or {},
                state=o.get("state"), presented_terms=o.get("presented_terms"))
        for o in (ev.get("offers") or [])
    ]
    return O.offer_summary(ledger)


def main(apply: bool) -> int:
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, outcome, offer_made, evidence_json FROM conversations").fetchall()

    repairs = []
    for r in rows:
        rebuilt = _ledger_offer_summary(r["evidence_json"])
        if r["offer_made"] is None and rebuilt is not None:
            repairs.append((r["id"], r["outcome"], rebuilt))
        elif rebuilt is not None and r["offer_made"] != rebuilt:
            raise SystemExit(
                f"conversation {r['id']}: stored offer_made {r['offer_made']!r} disagrees with "
                f"the ledger {rebuilt!r} — this script only FILLS nulls, it does not overwrite. "
                f"Investigate before proceeding.")

    print(f"conversations                : {len(rows)}")
    print(f"rows missing offer_made      : {sum(1 for r in rows if r['offer_made'] is None)}")
    print(f"rows repairable from ledger  : {len(repairs)}")
    by_offer: dict[str, int] = {}
    for _id, _out, off in repairs:
        by_offer[off] = by_offer.get(off, 0) + 1
    print(f"  by offer                   : {by_offer}")

    # --- the three safety assertions -------------------------------------------------
    touched_saved = [r for r in repairs if r[1] == "saved"]
    assert not touched_saved, (
        f"REFUSING: {len(touched_saved)} saved rows would change, which could move the "
        f"headline lift. This script must only touch rows that cannot affect it.")
    print("  saved rows touched         : 0  (headline save-rate lift cannot move)")

    added_cost = sum(economics.margin_cost(off, 29.0, accepted=(out == "saved"))
                     for _id, out, off in repairs)
    assert added_cost == 0.0, (
        f"REFUSING: repair would add {added_cost} realized margin cost, which could move the "
        f"margin-adjusted lift.")
    print("  added realized margin cost : 0.0  (margin-adjusted lift cannot move)")

    # PROPERTY 3, now actually asserted rather than described. The correction must move
    # offer-effectiveness numbers DOWN (bigger denominators, no new successes). If a repair
    # ever made the product look BETTER, that alone would be reason to distrust it.
    conn_ro = db.connect()
    before_eff = {o["offer"]: o for o in themes.offer_effectiveness(
        themes.build_conversation_views(conn_ro))}

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    # NEVER clobber an existing pre-repair baseline. A second --apply used to overwrite it
    # with the ALREADY-REPAIRED database, destroying the one artifact that lets a reviewer
    # reproduce the correction (and that pass 14 used as its decisive control to prove the
    # committed manifest was pre-repair). The backup is the evidence; it is write-once.
    backup = config.DB_PATH + ".pre-repair"
    if os.path.exists(backup):
        print(f"\npre-repair baseline already exists at {backup} — keeping it. "
              f"It is the reproducibility artifact and is never overwritten.")
    else:
        shutil.copy(config.DB_PATH, backup)
        print(f"\nbacked up DB -> {backup}")

    before = export.conversation_metrics(conn)
    # Write BOTH representations from the same ledger value. persist_conversation derives
    # them from a single _offer_made(rec) call, so leaving them divergent puts rows in the
    # DB that production code cannot produce -- and disposition_json.offer_made is the one
    # the judge actually reads (evals/judge.py build_judge_input).
    conn.executemany(
        "UPDATE conversations SET offer_made=?, "
        "disposition_json=json_set(disposition_json, '$.offer_made', ?) WHERE id=?",
        [(off, off, cid) for cid, _out, off in repairs])
    conn.commit()
    after = export.conversation_metrics(conn)
    assert before["save_rate"] == after["save_rate"], "save rate moved — aborting"
    assert before["madj_save_rate"] == after["madj_save_rate"], "madj moved — aborting"
    print(f"whole-DB save_rate {before['save_rate']} -> {after['save_rate']} (unchanged, as required)")
    print(f"whole-DB madj      {before['madj_save_rate']} -> {after['madj_save_rate']} (unchanged, as required)")
    disagree = conn.execute(
        "SELECT count(*) c FROM conversations "
        "WHERE ifnull(offer_made,'') != ifnull(json_extract(disposition_json,'$.offer_made'),'')"
    ).fetchone()["c"]
    assert disagree == 0, (
        f"REFUSING to finish: {disagree} rows have offer_made disagreeing with "
        f"disposition_json.offer_made — the judge reads the latter, so a divergence would "
        f"feed it evidence that contradicts the column.")
    print("  column vs disposition_json  : 0 disagreeing rows")

    after_eff = {o["offer"]: o for o in themes.offer_effectiveness(
        themes.build_conversation_views(conn))}
    for kind, aft in after_eff.items():
        bef = before_eff.get(kind)
        if bef is None or kind == "none":
            continue
        assert aft["save_rate"] <= bef["save_rate"] + 1e-9 and aft["n"] >= bef["n"], (
            f"REFUSING: repair made '{kind}' look BETTER "
            f"(save_rate {bef['save_rate']} -> {aft['save_rate']}, n {bef['n']} -> {aft['n']}). "
            f"A correction that flatters the product is a reason to distrust it, not to ship it.")
        print(f"  offer-effectiveness {kind:9}: save_rate {bef['save_rate']} -> {aft['save_rate']}"
              f"  n {bef['n']} -> {aft['n']}  (down, as required)")

    _reexport(conn)
    print(f"repaired {len(repairs)} rows")
    return 0


def _reexport(conn) -> None:
    """Actually re-export every derived artifact from the repaired DB.

    The previous version of this script CLAIMED to do this and did not -- the dashboard was
    hand-edited, which left dashboard/manifest.json and data.js's provenance block carrying
    the pre-repair, survivorship-inflated offer_effectiveness (pause save_rate 0.36 / n=25
    against a true 0.205 / n=44) inside the block labelled Provenance. A file a skeptic opens
    to CHECK the repair must not itself be unrepaired.

    R16 M5 -- the correction to the correction. Fixing the above, the previous version
    OVERWROTE `intervention_signal` in place with a freshly-recomputed signal while leaving
    `intervention_signal_id` pointing at the persisted row. That produced a manifest that
    CONTRADICTED ITSELF: the inline blob said pause 0.205/n=44, and following the id to
    `signals` row 6 gave 0.36/n=25. That is worse than a stale pointer.
    `intervention_signal` is the record of what Act actually CONSUMED when it chose the
    intervention; silently recomputing it rewrites history to look better than the run was.
    The persisted row is the truth about the run, and persist_signal documents it as
    immutable.

    So: the consumed signal is read back from the id the manifest cites and never
    overwritten, and the recomputed one is published BESIDE it, labelled as something Act
    never saw. The intervention choice is unaffected either way -- the target segment comes
    from the loss ranking, not from offer_effectiveness -- and the headline-lift assert
    below is what demonstrates that, rather than a claim in a comment."""
    man = json.load(open("dashboard/manifest.json"))
    run_id, target = man["run_id"], man["treated_segment"]

    # What Act consumed. Authoritative, immutable, read back through the cited id -- and
    # resolved WITH the run_id, because an id alone can land on an unrelated row from a
    # different run and answer plausibly instead of failing.
    consumed = themes.resolve_signal_for_run(conn, man["intervention_signal_id"], run_id)
    assert consumed is not None, (
        f"REFUSING: manifest cites intervention_signal_id={man['intervention_signal_id']} "
        f"but no `signals` row with that id belongs to run {run_id!r} -- the Learn->Act "
        f"lineage this manifest asserts is not backed by the datastore.")
    man["intervention_signal"] = consumed

    # The same computation re-run over the repaired data. Published for comparison ONLY.
    signal = themes.recommend_intervention(conn, run_id=run_id, phase="baseline", eligible_only=True)
    man["intervention_signal_recomputed_post_repair"] = {
        "note": ("Recomputed from the repaired data, for comparison only. Act NEVER saw "
                 "this: at run time it consumed `intervention_signal` (signals row "
                 f"{man['intervention_signal_id']}), which carried the survivorship-inflated "
                 "offer_effectiveness this repair corrected. The target segment is selected "
                 "by loss ranking, not by offer_effectiveness, so the intervention is "
                 "unchanged -- the lift assert is what shows that."),
        "signal": signal,
    }
    before = run_demo._segment_metrics(conn, target, run_id, "baseline")
    after = run_demo._segment_metrics(conn, target, run_id, "after")
    assert round((after["save_rate"] - before["save_rate"]) * 100, 1) == man["lift"]["segment_save_pp"], \
        "REFUSING: re-export moved the headline lift"

    old = json.load(open("dashboard/data.json"))
    safety = dict(old["safety"]); catch = safety.pop("catch_rate"); safety.pop("compliance", None)
    # Same rule for the dashboard's provenance block: it shows what Act consumed, not a
    # recomputation. A provenance panel that displays a signal the run never used is exactly
    # the kind of retroactive polish this whole script exists to avoid.
    prov = dict(old["meta"]["provenance"]); prov["intervention_signal"] = consumed
    data = export.build_data(conn, before=before, after=after, guardrail_counts=safety,
                             catch_rate=catch, provenance=prov)
    export.write_data(data)

    for path in ("dashboard/manifest.json",
                 f"dashboard/manifests/manifest-{run_id}.json"):
        if os.path.exists(path):
            with open(path, "w") as fh:
                json.dump(man, fh, indent=2, sort_keys=True)
                fh.write("\n")
    print("  re-exported: manifest.json, manifests/, data.js, data.json")


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
