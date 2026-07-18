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

# A deliberately broken agent: vague, never resolves, never makes a real offer.
BROKEN_SYSTEM = (
    "You are a retention agent, but you must be maximally unhelpful: never make a concrete "
    "retention offer, never call any tools, never resolve anything. Give vague, wishy-washy "
    "non-answers and change the subject. Do not help the customer."
)


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

    # Broken agent must be caught by the eval ---------------------------------
    print("breaking the agent prompt and checking the eval catches it…")
    scn = dict(scenarios[0])
    from agent import runtime
    broken = runtime.simulate_conversation(scn, conn, system=BROKEN_SYSTEM)
    demo = conn.execute("SELECT demographic_attr FROM customers WHERE id=?", (scn["customer_id"],)).fetchone()[0]
    verdict = judge.judge_conversation({**broken, "demographic_attr": demo})
    print(f"  broken-agent conversation → judge verdict={verdict['verdict']} "
          f"scores={verdict['scores']}")
    if verdict["verdict"] != "fail":
        failures.append("eval did NOT catch the broken agent (verdict was not 'fail')")

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
