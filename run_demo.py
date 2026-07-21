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

import hashlib
import json
import sys
from datetime import datetime, timezone

import batch
import config
import db
import economics
import synth
from agent import guardrails, policy, runtime
from analytics import themes
from dashboard import export
from evals import run_evals


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]

COHORT_PRICE = 8      # price-sensitive scenarios (the segment the act targets)
COHORT_OTHER = 10     # other churn reasons, for a representative population
WORKERS = 10
TARGET_REASON = "Price too high"   # the segment the discount act actually treats


def _segment_metrics(conn, reason: str) -> dict:
    """Save rate + margin-adjusted save rate for the conversations in ONE churn
    segment — the treated group, where an intervention's effect actually lands
    (measuring it on the whole cohort dilutes it with untreated customers)."""
    rows = conn.execute(
        "SELECT c.outcome, c.offer_made, s.price FROM conversations c "
        "JOIN scenarios sc ON sc.id = c.scenario_id "
        "JOIN subscriptions s ON s.customer_id = c.customer_id "
        "WHERE sc.churn_reason = ?", (reason,)).fetchall()
    n = len(rows) or 1
    saved = sum(1 for r in rows if r["outcome"] == "saved")
    madj = 0.0
    for r in rows:
        if r["outcome"] == "saved":
            cost = economics.margin_cost(r["offer_made"], r["price"], accepted=True)
            madj += 1 - (cost / r["price"] if r["price"] else 0)
    return {"n": len(rows), "save_rate": round(saved / n, 4), "madj_save_rate": round(madj / n, 4)}

IMPROVED_SYSTEM = runtime.SYSTEM + (
    "\n\nUPDATED PLAYBOOK (from analytics): for price-sensitive customers whose reason is "
    "'price too high', LEAD with a concrete discount offer before suggesting a pause — the data "
    "shows discounts retain this segment materially better than pauses.")


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
    cohort_ids = sorted(s["id"] for s in cohort)
    print(f"   cohort: {len(cohort)} customers ({COHORT_PRICE} price-sensitive + {COHORT_OTHER} other)\n")

    # ---- BEFORE: conservative policy, discounts disabled --------------------
    print("② BASELINE — discounts DISABLED (conservative launch policy)")
    policy.DISCOUNTS_ENABLED = False
    recs_a = batch.run_batch(conn, cohort, system=runtime.SYSTEM, max_workers=WORKERS)
    before, before_seg = export.conversation_metrics(conn), _segment_metrics(conn, TARGET_REASON)
    print(f"   overall save {before['save_rate']*100:.0f}%  ·  price-sensitive save {before_seg['save_rate']*100:.0f}%")
    themes.run_analytics(conn)
    print("③ LEARN — analytics signal:")
    print(f"   price-sensitive theme saves only {before_seg['save_rate']*100:.0f}% under the no-discount policy → "
          f"recommend enabling discounts for THIS segment.\n")

    # ---- ACT: enable discounts + improved playbook (TWO variables) ----------
    # Re-seed to an IDENTICAL fresh cohort (same seed) so cooldown state written
    # during batch A doesn't carry into batch B — each batch measures fresh state.
    print("④ ACT — enable discounts AND update the playbook to lead with a discount for price-sensitive customers")
    policy.DISCOUNTS_ENABLED = True
    synth.generate(conn)
    cohort2 = _cohort(conn)
    cohort2_ids = sorted(s["id"] for s in cohort2)

    print("⑤ RE-MEASURE — the same customers under the new policy + playbook")
    recs_b = batch.run_batch(conn, cohort2, system=IMPROVED_SYSTEM, max_workers=WORKERS)
    after, after_seg = export.conversation_metrics(conn), _segment_metrics(conn, TARGET_REASON)
    print(f"   overall save {after['save_rate']*100:.0f}%  ·  price-sensitive save {after_seg['save_rate']*100:.0f}%")

    print("   grading every conversation + re-clustering…")
    m = run_evals.grade_all(conn)
    themes.run_analytics(conn)
    counts, catch_rate = _redteam(conn)
    counts["over_limit"] = conn.execute(
        "SELECT count(*) FROM guardrail_events WHERE type='over_limit'").fetchone()[0]

    # Headline = the TREATED segment (where the discount act applies). Overall is
    # reported as context — it mixes in ~10 untreated customers and is noisier.
    seg_lift = (after_seg["save_rate"] - before_seg["save_rate"]) * 100
    seg_madj = (after_seg["madj_save_rate"] - before_seg["madj_save_rate"]) * 100
    overall_lift = (after["save_rate"] - before["save_rate"]) * 100
    paired = cohort_ids == cohort2_ids and len(recs_a) == len(cohort) and len(recs_b) == len(cohort2)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cohort_size": len(cohort), "cohort_scenario_ids": cohort_ids, "paired_cohort": paired,
        "treated_segment": TARGET_REASON,
        "variables_changed": ["discount_policy", "agent_playbook"],
        "models": {"flagship": config.FLAGSHIP_MODEL, "mini": config.MINI_MODEL, "embedding": config.EMBEDDING_MODEL},
        "baseline": {"policy": "discounts_disabled", "playbook_sha": _sha(runtime.SYSTEM), "conversations": len(recs_a),
                     "segment_save_rate": before_seg["save_rate"], "overall_save_rate": before["save_rate"]},
        "after": {"policy": "discounts_enabled", "playbook_sha": _sha(IMPROVED_SYSTEM), "conversations": len(recs_b),
                  "segment_save_rate": after_seg["save_rate"], "overall_save_rate": after["save_rate"],
                  "eval_pass_rate": m["eval_pass_rate"], "eval_coverage": m["coverage"]},
        "lift": {"segment_save_pp": round(seg_lift, 1), "segment_madj_pp": round(seg_madj, 1),
                 "overall_save_pp": round(overall_lift, 1)},
        "guardrail_catch_rate": round(catch_rate, 3),
        "note": ("Paired before/after on identical seeded customers. TWO variables changed together "
                 "(discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the "
                 "flywheel on the treated (price-sensitive) segment — not an isolated causal estimate. "
                 "Numbers vary run to run."),
    }
    with open("dashboard/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print("⑥ EXPORT — writing dashboard/data.js + dashboard/manifest.json")
    # dashboard trend shows the TREATED segment before/after — what the act moved
    data = export.export(conn, before=before_seg, after=after_seg, guardrail_counts=counts,
                         catch_rate=catch_rate, provenance=manifest)

    print("\n" + "=" * 72)
    print("FLYWHEEL RESULT  (synthetic paired demo — treated price-sensitive segment)")
    print(f"  price-sensitive save:   {before_seg['save_rate']*100:>5.0f}%  →  {after_seg['save_rate']*100:>5.0f}%   ({seg_lift:+.0f} pp)")
    print(f"  price-sensitive m-adj:  {before_seg['madj_save_rate']*100:>5.0f}%  →  {after_seg['madj_save_rate']*100:>5.0f}%   ({seg_madj:+.1f} pp)")
    print(f"  overall (context):      {before['save_rate']*100:>5.0f}%  →  {after['save_rate']*100:>5.0f}%   ({overall_lift:+.0f} pp)")
    print(f"  eval pass rate:       {m['eval_pass_rate']*100:.0f}% (coverage {m['coverage']*100:.0f}%)   ·   "
          f"guardrail catch rate: {catch_rate*100:.0f}%   ·   compliance: {data['kpis']['compliance_coverage']*100:.0f}%")
    print(f"  fairness gap:         {m['fairness_gap']}   ·   paired cohort: {paired}   ·   manifest: dashboard/manifest.json")
    print("=" * 72)

    ok = paired and seg_lift > 0 and data["meta"]["conversations"] > 0
    print("\nDEMO:", "the flywheel turned — treated-segment lift positive, paired cohort, provenance recorded." if ok
          else "did NOT meet the bar (needs a matched paired cohort AND a strictly positive treated-segment lift).")
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
