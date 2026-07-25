"""Keel — unit-economics model (plan §9, mirrors keel-economics.html).

Pure arithmetic, no API. The finding to preserve: ~97% of blended cost-to-serve
is human escalation; the entire AI stack (agent + grading every conversation +
guardrails) is ~3 cents/conversation. So the lever is *containment*, not model
choice, and you grade 100% because evaluation is nearly free.

Defaults are seeded from config.py (prices) and the plan's worked example.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import config


def margin_cost(offer_made: str | None, price: float, *, accepted: bool = True) -> float:
    """REALIZED dollar margin conceded by an offer (used by the analytics layer, §4).

    Only an *accepted* offer actually costs margin — a proposed-but-rejected offer
    costs nothing (`accepted=False` → 0). Discount → the % of price given away each
    month. Pause → a small goodwill fraction of price, scaled by the number of
    months paused (a 3-month pause costs 3× a 1-month pause)."""
    if not offer_made or not accepted:
        return 0.0
    nums = re.findall(r"(\d+)", offer_made)
    if "discount" in offer_made and nums:
        return round(price * float(nums[0]) / 100, 2)
    if "pause" in offer_made:
        months = int(nums[0]) if nums else 1
        return round(price * config.PAUSE_MARGIN_FRACTION * months, 2)
    return 0.0


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


def _validate(v: "Levers") -> None:
    """Reject inputs outside their valid domain, so the model never returns a nonsense number
    from a nonsense input (L1). Rates are probabilities in [0,1]; money/token counts are
    non-negative and finite."""
    for name in ("save_rate", "eval_sampling", "escalation_rate"):
        x = getattr(v, name)
        if not (isinstance(x, (int, float)) and math.isfinite(x) and 0.0 <= x <= 1.0):
            raise ValueError(f"{name} must be a probability in [0,1], got {x!r}")
    for name in ("conversations", "outcome_fee", "cost_per_escalation", "guardrail_infra",
                 "saved_ltv", "offer_cost", "agent_in_tok", "agent_out_tok", "eval_in_tok",
                 "eval_out_tok", "flagship_in", "flagship_out", "mini_in", "mini_out"):
        x = getattr(v, name)
        if not (isinstance(x, (int, float)) and math.isfinite(x) and x >= 0):
            raise ValueError(f"{name} must be a non-negative finite number, got {x!r}")


def compute(levers: Levers | None = None) -> dict:
    """Return the full derived economics for a lever set."""
    v = levers or Levers()
    _validate(v)

    agent = (v.agent_in_tok * v.flagship_in + v.agent_out_tok * v.flagship_out) / 1e6
    evalc = v.eval_sampling * (v.eval_in_tok * v.mini_in + v.eval_out_tok * v.mini_out) / 1e6
    other = v.guardrail_infra
    human = v.escalation_rate * v.cost_per_escalation
    automated = agent + evalc + other
    cpc = automated + human
    # With NO saves, cost-per-save and gross-margin-per-save are UNDEFINED (not 0 / 100%). Use
    # None → the CLI/dashboard render "N/A" rather than an impossible favorable number (L1).
    cps = cpc / v.save_rate if v.save_rate > 0 else None

    saves = v.conversations * v.save_rate
    revenue = saves * v.outcome_fee
    cost = v.conversations * cpc
    gross_profit = revenue - cost
    gross_margin = ((v.outcome_fee - cps) / v.outcome_fee) if (cps is not None and v.outcome_fee) else None
    # None, not 0.0. With no outcome fee there IS no break-even save rate, and 0.0 reads as
    # "break even immediately" — the best possible number produced by the worst possible
    # input. Same class as cost_per_save / gross_margin_per_save, which this module already
    # returns as None at zero saves.
    break_even_save = (cpc / v.outcome_fee) if v.outcome_fee else None

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
        "cost_per_save": round(cps, 4) if cps is not None else None,
        "gross_margin_per_save": round(gross_margin, 4) if gross_margin is not None else None,
        "break_even_save_rate": round(break_even_save, 4) if break_even_save is not None else None,
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
    cps, gm = r["cost_per_save"], r["gross_margin_per_save"]
    print(f"  cost/save              {'N/A (no saves)' if cps is None else f'${cps:.2f}'}")
    print(f"  gross margin/save      {'N/A (no saves)' if gm is None else f'{gm*100:.0f}%'}")
    print(f"  break-even save rate   {r['break_even_save_rate']*100:.1f}%")
    print(f"  vendor gross profit    ${r['vendor_pnl']['gross_profit']:,}")
    print(f"  customer return         {r['customer_roi']['return_multiple']}x")


if __name__ == "__main__":
    main()
