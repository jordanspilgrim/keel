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

The lift between BEFORE and AFTER — on identical seeded customers, changing TWO
variables together (the discount policy AND the agent playbook, both from the
analytics recommendation) — is the flywheel visibly turning. Because two variables
move together this is a paired demonstration, not an isolated causal estimate.

Run:  python run_demo.py
"""

from __future__ import annotations

import hashlib
import json
import os
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

# The treated cohort is n=60. At n=20 a single customer was 5pp, so a one-run lift
# was noise-dominated (observed swings from -10pp to +35pp across runs); at n=60 one
# customer is ~1.7pp, a materially tighter — and honest — read. This needs more
# price-sensitive scenarios than the default 200-customer synth provides, so the DEMO
# seeds a larger synthetic world (DEMO_SYNTH_N); the global synth size is unchanged.
DEMO_SYNTH_N = 400
COHORT_PRICE = 60     # price-sensitive scenarios (the treated segment)
COHORT_OTHER = 20     # other churn reasons, for a representative population
WORKERS = 12
# The treated segment is SELECTED from the baseline analytics signal (see main),
# not assumed. This is the reason the discount lever is designed to address; if
# the data ever pointed elsewhere, the demo says so and bails rather than pretend.
DISCOUNT_LEVER_REASON = "Price too high"


def _segment_metrics(conn, reason: str, run_id: str | None = None, phase: str | None = None) -> dict:
    """Save rate + margin-adjusted save rate for ONE churn segment (the treated
    group) within a specific experiment run/phase — so baseline and after are
    measured on their own conversations even though both live in the same DB."""
    where = ["sc.churn_reason = ?"]
    params: list = [reason]
    if run_id is not None:
        where.append("c.run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("c.phase = ?"); params.append(phase)
    rows = conn.execute(
        "SELECT c.outcome, c.offer_made, s.price FROM conversations c "
        "JOIN scenarios sc ON sc.id = c.scenario_id "
        "JOIN subscriptions s ON s.customer_id = c.customer_id "
        "WHERE " + " AND ".join(where), params).fetchall()
    n = len(rows) or 1
    saved = sum(1 for r in rows if r["outcome"] == "saved")
    madj = 0.0
    for r in rows:
        if r["outcome"] == "saved":
            cost = economics.margin_cost(r["offer_made"], r["price"], accepted=True)
            madj += 1 - (cost / r["price"] if r["price"] else 0)
    return {"n": len(rows), "save_rate": round(saved / n, 4), "madj_save_rate": round(madj / n, 4)}

def _improved_system(reason: str) -> str:
    """The updated playbook the ACT step applies — parameterized by the segment the
    analytics signal selected, so the intervention tracks the data, not a constant."""
    return runtime.SYSTEM + (
        f"\n\nUPDATED PLAYBOOK (from analytics): for customers whose reason is "
        f"'{reason.lower()}', LEAD with a concrete discount offer before suggesting a pause — the "
        f"data shows discounts retain this segment materially better than pauses.")


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
    rate = caught / total if total else 0.0
    # Persist so the kill switch (safety.program_state) can gate on the latest
    # red-team result without recomputing it (an LLM call per probe) on every poll.
    # Tag it with the guardrail version so a later guardrail change invalidates it.
    db.record_health(conn, "guardrail_catch_rate", rate, f"{caught}/{total} probes caught",
                     version=config.GUARDRAIL_VERSION)
    return counts, rate


_SNAPSHOT_COLS = ("customer_id", "plan", "price", "status", "last_save_offer_days")


def _snapshot_world(conn, customer_ids: list[int]) -> list[dict]:
    """Capture the EXACT starting subscription state for the cohort's customers. Both
    arms of the paired comparison run from this same snapshot, so eligibility (and every
    other subscription field) is held identical across arms — the ONLY things that
    differ between baseline and after are the policy and the playbook."""
    marks = ",".join("?" for _ in customer_ids)
    rows = conn.execute(
        f"SELECT {', '.join(_SNAPSHOT_COLS)} FROM subscriptions WHERE customer_id IN ({marks}) "
        f"ORDER BY customer_id", customer_ids).fetchall()
    return [dict(r) for r in rows]


def _restore_world(conn, snapshot: list[dict]) -> None:
    """Restore subscriptions to a snapshot exactly — undoing any mutation the baseline
    arm made (e.g. an accepted save sets the cooldown to 0). After this, the after arm
    starts from a world byte-identical to the one the baseline arm started from."""
    for row in snapshot:
        conn.execute(
            "UPDATE subscriptions SET plan=?, price=?, status=?, last_save_offer_days=? WHERE customer_id=?",
            (row["plan"], row["price"], row["status"], row["last_save_offer_days"], row["customer_id"]))
    conn.commit()


def _world_hash(snapshot: list[dict]) -> str:
    """A content hash of the cohort's starting subscription state. Recorded for BOTH
    arms in the manifest; identical hashes are the proof the comparison started from the
    same world, so a reader can verify eligibility wasn't changed between arms."""
    blob = json.dumps([[r[c] for c in _SNAPSHOT_COLS] for r in snapshot], sort_keys=True)
    return "world-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def main() -> int:
    conn = db.connect()
    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    print(f"① generate — seeded synthetic customers  (run {run_id})")
    synth.generate(conn, n=DEMO_SYNTH_N)  # larger world so the treated cohort can be n=60
    cohort = _cohort(conn)
    cohort_ids = sorted(s["id"] for s in cohort)
    customer_ids = [s["customer_id"] for s in cohort]
    # Capture the EXACT starting world. Both arms run from this snapshot, so eligibility
    # is held constant — the only things that differ are the policy and the playbook.
    world0 = _snapshot_world(conn, customer_ids)
    world_sha = _world_hash(world0)
    print(f"   cohort: {len(cohort)} customers ({COHORT_PRICE} price-sensitive + {COHORT_OTHER} other)  ·  "
          f"starting-state {world_sha}\n")

    # ---- BEFORE: conservative policy, discounts disabled --------------------
    print("② BASELINE — discounts DISABLED (conservative launch policy)")
    policy.DISCOUNTS_ENABLED = False
    recs_a = batch.run_batch(conn, cohort, system=runtime.SYSTEM, max_workers=WORKERS,
                             run_id=run_id, phase="baseline")
    before = export.conversation_metrics(conn, run_id=run_id, phase="baseline")

    # ---- LEARN: cluster the VoC, then CONSUME a structured intervention signal --
    themes.run_analytics(conn)  # embed → cluster → theme cards → ranked signals (persisted)
    signal = themes.recommend_intervention(conn)  # structured: segment + lever + confidence + evidence
    print("③ LEARN — structured intervention signal (analytics proposes what to do):")
    for s in signal["evidence"].get("segment_ranking", []):
        print(f"     {s['reason']:<22} save {s['save_rate']*100:>3.0f}%  ·  n={s['n']}  ·  loss={s['loss']}")
    print(f"   signal: segment='{signal['segment']}' · lever={signal['recommended_lever']} · "
          f"confidence={signal['confidence']} · action: {signal['recommended_action']}")
    # Act consumes the signal. If analytics recommends a segment no available lever
    # fits, the demo BAILS rather than misattribute a lift to the wrong lever.
    if not signal["lever_compatible"]:
        print(f"\n   ⚠ no available policy lever fits '{signal['segment']}' — the honest move is a "
              f"different intervention. Demo bails rather than misattribute.")
        conn.close()
        return 1
    target = signal["segment"]
    before_seg = _segment_metrics(conn, target, run_id, "baseline")
    # Persist the signal under this run_id and load it back by id, so Act consumes
    # the signal THROUGH the datastore (durable Learn→Act lineage).
    signal_id = themes.persist_signal(conn, signal, run_id=run_id)
    signal = themes.load_signal(conn, signal_id)
    target = signal["segment"]

    # ---- ACT: enable discounts + improved playbook (TWO variables) ----------
    # RESTORE the exact starting world so the after arm begins byte-identical to the
    # baseline arm — undoing any subscription mutation baseline made (e.g. an accepted
    # save sets the cooldown to 0). This is what makes eligibility a held constant
    # rather than a hidden confound: the only variables that change are policy + playbook.
    improved_system = _improved_system(target)
    print(f"④ ACT — consuming persisted signal #{signal_id}: enable discounts + lead-with-discount "
          f"playbook for the '{target}' segment")
    policy.DISCOUNTS_ENABLED = True
    _restore_world(conn, world0)
    world_after_sha = _world_hash(_snapshot_world(conn, customer_ids))
    identical_start = world_after_sha == world_sha  # proof the after arm starts from the same world
    if not identical_start:
        print(f"   ⚠ starting-state mismatch ({world_after_sha} != {world_sha}) — the comparison would "
              f"not be apples-to-apples. Demo bails rather than report a confounded lift.")
        conn.close()
        return 1
    print(f"   restored starting-state {world_after_sha} (identical to baseline) — eligibility held constant")
    cohort2 = _cohort(conn)  # SAME scenarios (not re-seeded) → paired by construction
    cohort2_ids = sorted(s["id"] for s in cohort2)

    print("⑤ RE-MEASURE — the same customers under the new policy + playbook")
    recs_b = batch.run_batch(conn, cohort2, system=improved_system, max_workers=WORKERS,
                             run_id=run_id, phase="after")
    after = export.conversation_metrics(conn, run_id=run_id, phase="after")
    after_seg = _segment_metrics(conn, target, run_id, "after")
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
    paired = (cohort_ids == cohort2_ids and len(recs_a) == len(cohort)
              and len(recs_b) == len(cohort2) and identical_start)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "cohort_size": len(cohort), "cohort_scenario_ids": cohort_ids, "paired_cohort": paired,
        "treated_segment": target,
        "segment_selection": "data-driven (structured intervention signal consumed by Act)",
        "intervention_signal_id": signal_id,
        "intervention_signal": signal,
        "guardrail_version": config.GUARDRAIL_VERSION,
        "variables_changed": ["discount_policy", "agent_playbook"],
        "held_constant": ["cohort_scenarios", "starting_subscription_state", "eligibility"],
        "identical_starting_state": identical_start,
        "starting_state_sha": world_sha,
        "models": {"flagship": config.FLAGSHIP_MODEL, "mini": config.MINI_MODEL, "embedding": config.EMBEDDING_MODEL},
        "baseline": {"policy": "discounts_disabled", "playbook_sha": _sha(runtime.SYSTEM), "conversations": len(recs_a),
                     "starting_state_sha": world_sha,
                     "segment_save_rate": before_seg["save_rate"], "overall_save_rate": before["save_rate"]},
        "after": {"policy": "discounts_enabled", "playbook_sha": _sha(improved_system), "conversations": len(recs_b),
                  "starting_state_sha": world_after_sha,
                  "segment_save_rate": after_seg["save_rate"], "overall_save_rate": after["save_rate"],
                  "eval_pass_rate": m["eval_pass_rate"], "eval_coverage": m["coverage"]},
        "lift": {"segment_save_pp": round(seg_lift, 1), "segment_madj_pp": round(seg_madj, 1),
                 "overall_save_pp": round(overall_lift, 1)},
        "guardrail_catch_rate": round(catch_rate, 3),
        "note": ("Paired before/after on identical seeded customers run from a byte-identical starting "
                 "subscription state (same starting_state_sha in both arms → eligibility held constant). "
                 "TWO variables changed together (discount policy + agent playbook), so this is a synthetic "
                 "PAIRED demonstration of the flywheel on the treated (price-sensitive) segment — not an "
                 "isolated causal estimate. Numbers vary run to run."),
    }
    with open("dashboard/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    # Retain a DATED copy of every run's manifest, so any range/consistency claim
    # in the docs is backed by a persisted artifact (not just prose).
    os.makedirs("dashboard/manifests", exist_ok=True)
    stamp = manifest["generated_at"].replace(":", "").replace("-", "").split(".")[0]
    with open(f"dashboard/manifests/manifest-{stamp}.json", "w") as f:
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
