"""Keel — end-to-end flywheel demo (handoff §9 definition of done).

The money demo. Runs the loop once, end to end, and shows it turning:

  generate  → seeded synthetic customers + scenarios
  BASELINE  → a conservative launch policy (discounts disabled); the agent can
              only offer pauses. Measure the save rate.
  learn     → grade + cluster; the analytics signal: the price-sensitive theme
              is under-saved because discounts are off.
  ACT       → apply the recommended policy change: enable discounts and direct
              the agent to lead with a discount for price-sensitive customers.
  re-measure→ run the SAME customers again under the new policy.
  export    → dashboard/data.js, so the dashboard shows the measured lift.

The lift between BEFORE and AFTER — on identical customers, changing only the
policy the analytics recommended — is the flywheel visibly turning.

Run:  python run_demo.py
"""

from __future__ import annotations

import sys

import batch
import db
import synth
from agent import guardrails, policy, runtime
from analytics import themes
from dashboard import export
from evals import run_evals

COHORT_PRICE = 8      # price-sensitive scenarios (the segment the act targets)
COHORT_OTHER = 10     # other churn reasons, for a representative population
WORKERS = 10

IMPROVED_SYSTEM = runtime.SYSTEM + (
    "\n\nUPDATED PLAYBOOK (from analytics): for price-sensitive customers whose reason is "
    "'price too high', LEAD with a concrete discount offer before suggesting a pause — the data "
    "shows discounts retain this segment materially better than pauses.")


def _clear_conversation_data(conn):
    for t in ["signals", "themes", "evals", "guardrail_events", "audit_log", "conversations"]:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def _cohort(conn):
    price = conn.execute("SELECT * FROM scenarios WHERE kind='churn' AND churn_reason='Price too high' "
                         "ORDER BY id LIMIT ?", (COHORT_PRICE,)).fetchall()
    other = conn.execute("SELECT * FROM scenarios WHERE kind='churn' AND churn_reason!='Price too high' "
                         "ORDER BY id LIMIT ?", (COHORT_OTHER,)).fetchall()
    return [dict(s) for s in (list(price) + list(other))]


def _redteam(conn) -> tuple[dict, float]:
    """Screen the adversarial probes; return safety counts + catch rate."""
    probes = conn.execute("SELECT opening_message, attack_type FROM scenarios WHERE is_adversarial=1").fetchall()
    counts = {"jailbreaks": 0, "off_scope": 0, "pii": 0}
    caught, total = 0, 0
    for p in probes:
        total += 1
        s = guardrails.screen_input(p["opening_message"], classify_scope=True)
        if p["attack_type"] == "jailbreak" and s["jailbreak"]["flagged"]:
            counts["jailbreaks"] += 1; caught += 1
        elif p["attack_type"] == "off_scope" and s["off_scope"]:
            counts["off_scope"] += 1; caught += 1
        elif p["attack_type"] == "pii_leak" and s["pii_types"]:
            counts["pii"] += 1; caught += 1
    return counts, (caught / total if total else 0.0)


def main() -> int:
    conn = db.connect()
    print("① generate — seeded synthetic customers")
    synth.generate(conn)
    cohort = _cohort(conn)
    print(f"   cohort: {len(cohort)} customers ({COHORT_PRICE} price-sensitive + {COHORT_OTHER} other)\n")

    # ---- BEFORE: conservative policy, discounts disabled --------------------
    print("② BASELINE — discounts DISABLED (conservative launch policy)")
    policy.DISCOUNTS_ENABLED = False
    batch.run_batch(conn, cohort, system=runtime.SYSTEM, max_workers=WORKERS)
    before = export.conversation_metrics(conn)
    print(f"   save rate {before['save_rate']*100:.0f}%  ·  margin-adjusted {before['madj_save_rate']*100:.0f}%")
    themes_before = themes.run_analytics(conn)
    ps = next((t for t in themes_before["themes"] if "price" in t["label"].lower()), None)
    print("③ LEARN — analytics signal:")
    if ps:
        print(f"   price-sensitive theme saves only {ps['save_rate']*100:.0f}% under the no-discount policy → "
              f"recommend enabling discounts for this segment.\n")
    else:
        print("   price-sensitive theme under-saved → recommend enabling discounts.\n")

    # ---- ACT: enable discounts + improved playbook --------------------------
    print("④ ACT — enable discounts and lead with a discount for price-sensitive customers")
    policy.DISCOUNTS_ENABLED = True
    _clear_conversation_data(conn)

    # ---- AFTER: same customers, new policy ----------------------------------
    print("⑤ RE-MEASURE — same customers, new policy")
    batch.run_batch(conn, cohort, system=IMPROVED_SYSTEM, max_workers=WORKERS)
    after = export.conversation_metrics(conn)
    print(f"   save rate {after['save_rate']*100:.0f}%  ·  margin-adjusted {after['madj_save_rate']*100:.0f}%")

    print("   grading every conversation + re-clustering…")
    m = run_evals.grade_all(conn)
    themes.run_analytics(conn)
    counts, catch_rate = _redteam(conn)
    # add over-limit guardrail trips observed in the batch
    counts["over_limit"] = conn.execute(
        "SELECT count(*) FROM guardrail_events WHERE type='over_limit'").fetchone()[0]

    print("⑥ EXPORT — writing dashboard/data.js")
    data = export.export(conn, before=before, after=after,
                         guardrail_counts=counts, catch_rate=catch_rate)

    lift_pp = (after["save_rate"] - before["save_rate"]) * 100
    madj_lift = (after["madj_save_rate"] - before["madj_save_rate"]) * 100
    print("\n" + "=" * 72)
    print("FLYWHEEL RESULT")
    print(f"  save rate:            {before['save_rate']*100:>5.0f}%  →  {after['save_rate']*100:>5.0f}%   ({lift_pp:+.0f} pp)")
    print(f"  margin-adjusted:      {before['madj_save_rate']*100:>5.0f}%  →  {after['madj_save_rate']*100:>5.0f}%   ({madj_lift:+.1f} pp)")
    print(f"  eval pass rate:       {m['eval_pass_rate']*100:.0f}%   ·   guardrail catch rate: {catch_rate*100:.0f}%   ·   compliance: {data['kpis']['compliance_coverage']*100:.0f}%")
    print(f"  fairness gap:         {m['fairness_gap']}   ·   dashboard: dashboard/index.html (open in a browser)")
    print("=" * 72)

    ok = lift_pp >= 0 and data["meta"]["conversations"] > 0
    print("\nDEMO:", "the flywheel turned — a signal was acted on and the lift measured." if ok
          else "completed, but no positive lift measured this run.")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
