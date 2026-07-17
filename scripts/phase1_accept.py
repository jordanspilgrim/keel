"""Phase 1 acceptance — live end-to-end run (spends a few cents of OpenAI tokens).

Runs one ELIGIBLE and one INELIGIBLE churn scenario through the agent and
asserts the Phase 1 gate:
  - the AI disclosure is present in every transcript;
  - no offer ever exceeds policy limits;
  - the ineligible customer gets NO authorized save offer (cooldown) and is not
    "saved"; the eligible customer receives an authorized offer.

Run:  python -m scripts.phase1_accept
"""

from __future__ import annotations

import json
import sys

import config
import db
import synth
from agent import disclosure, runtime


def _pick(conn):
    """One eligible and one ineligible churn scenario (deterministic: lowest id each)."""
    elig = conn.execute(
        "SELECT * FROM scenarios WHERE kind='churn' AND eligible=1 ORDER BY id LIMIT 1"
    ).fetchone()
    inelig = conn.execute(
        "SELECT * FROM scenarios WHERE kind='churn' AND eligible=0 ORDER BY id LIMIT 1"
    ).fetchone()
    return dict(elig), dict(inelig)


def _print_convo(tag, res):
    print(f"\n{'='*72}\n{tag}: conversation #{res['conversation_id']} → outcome={res['outcome']} "
          f"offer={res['offer_made']!r}\n{'='*72}")
    for t in res["transcript"]:
        who = {"assistant": "AGENT", "user": "CUSTOMER"}.get(t["role"], t["role"].upper())
        print(f"  {who}: {t['content']}")
    print("  disposition:", json.dumps(res["disposition"]))
    if res["policy_decisions"]:
        print("  policy decisions:", [f"{d['tool']}:{d['action']}" for d in res["policy_decisions"]])


def main() -> int:
    conn = db.connect()
    synth.generate(conn)  # fresh, reproducible data
    elig, inelig = _pick(conn)
    print(f"eligible scenario: cust #{elig['customer_id']} reason={elig['churn_reason']!r}")
    print(f"ineligible scenario: cust #{inelig['customer_id']} reason={inelig['churn_reason']!r}")

    res_e = runtime.run_conversation(elig, conn)
    _print_convo("ELIGIBLE", res_e)
    res_i = runtime.run_conversation(inelig, conn)
    _print_convo("INELIGIBLE", res_i)

    # ---- assertions (the gate) --------------------------------------------
    failures = []
    for tag, res in [("eligible", res_e), ("ineligible", res_i)]:
        if not disclosure.has_disclosure(res["transcript"]):
            failures.append(f"{tag}: AI disclosure missing")
        # no offer exceeds limits: every authorized offer decision is ok/capped within ceilings
        for d in res["policy_decisions"]:
            if d["tool"] == "offer_discount" and d["allowed"] and d["args"]["pct"] > config.MAX_DISCOUNT_PCT:
                failures.append(f"{tag}: discount {d['args']['pct']} exceeds {config.MAX_DISCOUNT_PCT}%")
            if d["tool"] == "offer_pause" and d["allowed"] and d["args"]["months"] > config.MAX_PAUSE_MONTHS:
                failures.append(f"{tag}: pause {d['args']['months']} exceeds {config.MAX_PAUSE_MONTHS}mo")

    # ineligible: no save offer authorized, not saved
    inelig_save = [d for d in res_i["policy_decisions"]
                   if d["tool"] in ("offer_discount", "offer_pause") and d["allowed"]]
    if inelig_save:
        failures.append(f"ineligible: a save offer was authorized during cooldown: {inelig_save}")
    if res_i["outcome"] == "saved":
        failures.append("ineligible: outcome was 'saved' (should churn/escalate under cooldown)")
    # eligible: an offer was authorized
    if res_e["offer_made"] is None and res_e["outcome"] != "escalated":
        failures.append("eligible: no retention offer was authorized")

    print("\n" + "#" * 72)
    if failures:
        print("PHASE 1 ACCEPTANCE: FAIL")
        for f in failures:
            print("  ✗", f)
        return 1
    print("PHASE 1 ACCEPTANCE: PASS")
    print(f"  eligible   → {res_e['outcome']} (offer: {res_e['offer_made']})")
    print(f"  ineligible → {res_i['outcome']} (no save offer authorized during cooldown)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
