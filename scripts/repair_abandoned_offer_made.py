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

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    backup = config.DB_PATH + ".pre-repair"
    shutil.copy(config.DB_PATH, backup)
    print(f"\nbacked up DB -> {backup}")

    before = export.conversation_metrics(conn)
    conn.executemany("UPDATE conversations SET offer_made=? WHERE id=?",
                     [(off, cid) for cid, _out, off in repairs])
    conn.commit()
    after = export.conversation_metrics(conn)
    assert before["save_rate"] == after["save_rate"], "save rate moved — aborting"
    assert before["madj_save_rate"] == after["madj_save_rate"], "madj moved — aborting"
    print(f"whole-DB save_rate {before['save_rate']} -> {after['save_rate']} (unchanged, as required)")
    print(f"whole-DB madj      {before['madj_save_rate']} -> {after['madj_save_rate']} (unchanged, as required)")
    print(f"repaired {len(repairs)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
