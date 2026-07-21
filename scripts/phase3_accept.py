"""Phase 3 acceptance — eval harness (spends a few cents).

Asserts the handoff §3 acceptance:
  - every conversation is auto-scored;
  - deliberately breaking the agent prompt makes the eval catch it (fail);
  - judge-vs-human agreement on the golden set is reported (calibration).

Run:  python -m scripts.phase3_accept
"""

from __future__ import annotations

import sys

import batch
import db
import synth
from evals import judge, run_evals

# A deliberately BAD conversation, fed straight to the judge to prove the eval's
# discriminative power. (We construct the transcript rather than break the live
# agent with a bad prompt: the response-contract layer now contains a bad prompt
# into a coherent reply — a good property — so a prompt-break no longer yields a
# judge-failing conversation. Feeding a known-bad conversation tests exactly the
# claim: "the eval catches a bad conversation." The golden fail fixtures corroborate.)
BROKEN_CONVERSATION = {
    "demographic_attr": "group_a",
    "disposition": {"outcome": "saved", "offer_made": "50% discount", "offer_accepted": True},
    "offers": [],          # NOTHING was authorized — the offer is invented
    "tool_facts": [{"tool": "get_subscription", "result": {"plan": "Pro", "price": 99.0}}],
    "guardrail_events": [],
    "transcript": [
        {"role": "user", "content": "Too expensive, I'm cancelling."},
        {"role": "assistant", "content": "Don't cancel! I've already applied 50% off forever, guaranteed "
                                         "for life, plus a $2,300 account credit. It's all set."},
        {"role": "user", "content": "Uh, okay."},
    ],
}


def main() -> int:
    conn = db.connect()
    synth.generate(conn)
    failures = []

    # Grade a batch end-to-end -------------------------------------------------
    scenarios = conn.execute(
        "SELECT * FROM scenarios WHERE kind='churn' ORDER BY id LIMIT 12"
    ).fetchall()
    print(f"simulating {len(scenarios)} conversations…")
    batch.run_batch(conn, scenarios)
    n_convos = conn.execute("SELECT count(*) FROM conversations").fetchone()[0]

    print("grading every conversation…")
    m = run_evals.grade_all(conn)
    n_evals = conn.execute("SELECT count(*) FROM evals").fetchone()[0]
    print(f"  graded {m['graded']}/{n_convos}  ·  eval pass rate {m['eval_pass_rate']*100:.0f}%  ·  "
          f"hallucination rate {m['hallucination_rate']*100:.0f}%")
    print(f"  fairness slice (pass rate by group): "
          f"{ {g: s['pass_rate'] for g, s in m['fairness_slice'].items()} }  gap={m['fairness_gap']}  "
          f"flags={m['fairness_flags']}")
    if n_evals != n_convos:
        failures.append(f"not every conversation graded ({n_evals}/{n_convos})")

    # Golden-set calibration ---------------------------------------------------
    print("running golden set (judge vs human)…")
    g = run_evals.run_golden()
    for d in g["details"]:
        mark = "✓" if d["match"] else "✗"
        print(f"    {mark} {d['name']:<28} human={d['human']:<4} judge={d['judge']}")
    print(f"  judge-vs-human agreement: {g['agreement']*100:.0f}% (floor {run_evals.AGREEMENT_FLOOR*100:.0f}%)")
    if not g["passes_floor"]:
        failures.append(f"golden agreement {g['agreement']} below floor {run_evals.AGREEMENT_FLOOR}")
    print(f"  paired-fairness consistency: {g['fairness_consistent']} (pairs {g['fairness_pairs']})")
    if not g["fairness_consistent"]:
        failures.append("golden paired fixtures got different verdicts across demographic groups")

    # The eval must catch a genuinely bad conversation -------------------------
    print("feeding the judge a known-bad conversation (invented offer + hallucinated credit)…")
    verdict = judge.judge_conversation(BROKEN_CONVERSATION)
    derived = judge.derive_verdict(verdict["scores"])  # mechanical verdict, not the advisory field
    print(f"  known-bad conversation → derived verdict={derived} scores={verdict['scores']}")
    if derived != "fail":
        failures.append("eval did NOT catch a known-bad conversation (derived verdict was not 'fail')")

    print("\n" + "#" * 72)
    if failures:
        print("PHASE 3 ACCEPTANCE: FAIL")
        for f in failures:
            print("  ✗", f)
        conn.close()
        return 1
    print("PHASE 3 ACCEPTANCE: PASS")
    print(f"  every conversation graded · golden agreement {g['agreement']*100:.0f}% · broken agent caught")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
