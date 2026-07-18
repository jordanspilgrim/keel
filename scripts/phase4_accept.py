"""Phase 4 acceptance — VoC analytics (spends a few cents).

Asserts the handoff §3 acceptance:
  - produces the top churn drivers, and
  - a comparison like "20%-off saves ~8pp more than pause but costs ~3× margin"
  — all from clustered data only.

Run:  python -m scripts.phase4_accept
"""

from __future__ import annotations

import sys

import batch
import db
import synth
from analytics import themes

BATCH_N = 30


def main() -> int:
    conn = db.connect()
    synth.generate(conn)
    failures = []

    scenarios = conn.execute(
        "SELECT * FROM scenarios WHERE kind='churn' ORDER BY id LIMIT ?", (BATCH_N,)
    ).fetchall()
    print(f"simulating {len(scenarios)} conversations…")
    batch.run_batch(conn, scenarios)

    print("running analytics (embed → cluster → themes → signals)…")
    out = themes.run_analytics(conn)

    print("\nTop churn drivers (largest themes):")
    for c in out["themes"][:3]:
        print(f"  • {c['label']:<28} n={c['size']:<3} save_rate={c['save_rate']*100:.0f}% "
              f"avg_margin_cost=${c['avg_margin_cost']}")

    print("\nOffer effectiveness:")
    for o in out["offers"]:
        print(f"  • {o['offer']:<10} n={o['n']:<3} save_rate={o['save_rate']*100:.0f}% "
              f"avg_margin_cost=${o['avg_margin_cost']}")

    print("\nTop ranked signals:")
    for s in out["signals"][:3]:
        print(f"  • [{s['priority_score']}] {s['recommendation']}")

    # the money comparison, computed from data
    by = {o["offer"]: o for o in out["offers"]}
    if "discount" in by and "pause" in by:
        d, p = by["discount"], by["pause"]
        pp = (d["save_rate"] - p["save_rate"]) * 100
        ratio = (d["avg_margin_cost"] / p["avg_margin_cost"]) if p["avg_margin_cost"] else float("inf")
        print(f"\n  → Discount saves {pp:+.0f}pp vs pause, at {ratio:.1f}× the margin cost.")
    else:
        print(f"\n  → Only offer types present: {list(by)} (need both discount & pause to compare)")
        failures.append("offer comparison needs both discount and pause present")

    # assertions
    if len(out["themes"]) < 3:
        failures.append(f"expected ≥3 themes, got {len(out['themes'])}")
    if conn.execute("SELECT count(*) FROM themes").fetchone()[0] < 3:
        failures.append("themes not persisted")
    if conn.execute("SELECT count(*) FROM signals").fetchone()[0] < 1:
        failures.append("signals not persisted")

    print("\n" + "#" * 72)
    if failures:
        print("PHASE 4 ACCEPTANCE: FAIL")
        for f in failures:
            print("  ✗", f)
        conn.close()
        return 1
    print("PHASE 4 ACCEPTANCE: PASS")
    print(f"  {len(out['themes'])} themes · {len(out['signals'])} signals · offer comparison computed from clusters")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
