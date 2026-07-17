"""Keel — unit-economics model (plan §9, mirrors keel-economics.html).

Pure arithmetic, no API. The finding to preserve: ~97% of blended cost-to-serve
is human escalation; the entire AI stack (agent + grading every conversation +
guardrails) is ~3 cents/conversation. So the lever is *containment*, not model
choice, and you grade 100% because evaluation is nearly free.

Defaults are seeded from config.py (prices) and the plan's worked example.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import config


@dataclass
class Levers:
    # volume & rates
    conversations: int = 100_000
    save_rate: float = 0.40
    escalation_rate: float = 0.25
    cost_per_escalation: float = 5.00
    # pricing / value (USD)
    outcome_fee: float = 15.0          # vendor revenue per successful save
    offer_cost: float = 40.0           # margin given away per save
    saved_ltv: float = 300.0           # gross value of a save (customer LTV)
    # token assumptions (per conversation)
    agent_in_tok: int = 12_000
    agent_out_tok: int = 1_500
    eval_sampling: float = 1.00        # grade 100%
    eval_in_tok: int = 3_000
    eval_out_tok: int = 400
    guardrail_infra: float = 0.002     # guardrail + embedding + infra, per conv
    # prices (per 1M tokens) — default to config
    flagship_in: float = config.FLAGSHIP_PRICE_IN
    flagship_out: float = config.FLAGSHIP_PRICE_OUT
    mini_in: float = config.MINI_PRICE_IN
    mini_out: float = config.MINI_PRICE_OUT


def compute(levers: Levers | None = None) -> dict:
    """Return the full derived economics for a lever set."""
    v = levers or Levers()

    agent = (v.agent_in_tok * v.flagship_in + v.agent_out_tok * v.flagship_out) / 1e6
    evalc = v.eval_sampling * (v.eval_in_tok * v.mini_in + v.eval_out_tok * v.mini_out) / 1e6
    other = v.guardrail_infra
    human = v.escalation_rate * v.cost_per_escalation
    automated = agent + evalc + other
    cpc = automated + human
    cps = cpc / v.save_rate if v.save_rate else 0.0

    saves = v.conversations * v.save_rate
    revenue = saves * v.outcome_fee
    cost = v.conversations * cpc
    gross_profit = revenue - cost
    gross_margin = (v.outcome_fee - cps) / v.outcome_fee if v.outcome_fee else 0.0
    break_even_save = cpc / v.outcome_fee if v.outcome_fee else 0.0

    net_value = v.saved_ltv - v.offer_cost
    value_retained = saves * net_value
    roi_multiple = value_retained / revenue if revenue else 0.0

    return {
        "cost_per_conversation": round(cpc, 4),
        "cost_breakdown": {
            "human_escalation": round(human, 4),
            "agent_llm": round(agent, 4),
            "eval": round(evalc, 4),
            "guardrail_infra": round(other, 4),
        },
        "automated_subtotal": round(automated, 4),
        "human_pct_of_cost": round(human / cpc * 100, 1) if cpc else 0.0,
        "cost_per_save": round(cps, 4),
        "gross_margin_per_save": round(gross_margin, 4),
        "break_even_save_rate": round(break_even_save, 4),
        "vendor_pnl": {
            "saves": round(saves),
            "revenue": round(revenue),
            "cost_to_serve": round(cost),
            "gross_profit": round(gross_profit),
        },
        "customer_roi": {
            "net_value_per_save": round(net_value, 2),
            "value_retained": round(value_retained),
            "fees_paid": round(revenue),
            "return_multiple": round(roi_multiple, 1),
        },
    }


def main() -> None:
    r = compute()
    b = r["cost_breakdown"]
    print("Keel unit economics (defaults):")
    print(f"  cost/conversation      ${r['cost_per_conversation']:.2f}")
    print(f"    human escalation     ${b['human_escalation']:.4f}  ({r['human_pct_of_cost']}% of cost)")
    print(f"    agent LLM            ${b['agent_llm']:.4f}")
    print(f"    eval (100% graded)   ${b['eval']:.5f}")
    print(f"    guardrail + infra    ${b['guardrail_infra']:.4f}")
    print(f"  AI stack subtotal      ${r['automated_subtotal']:.4f}")
    print(f"  cost/save              ${r['cost_per_save']:.2f}")
    print(f"  gross margin/save      {r['gross_margin_per_save']*100:.0f}%")
    print(f"  break-even save rate   {r['break_even_save_rate']*100:.1f}%")
    print(f"  vendor gross profit    ${r['vendor_pnl']['gross_profit']:,}")
    print(f"  customer return         {r['customer_roi']['return_multiple']}x")


if __name__ == "__main__":
    main()
