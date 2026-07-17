"""Deterministic policy / authorization layer (plan §4 — the most important).

The model *proposes* an action; this layer *disposes*. Pure Python, no LLM, so
it cannot be talked past. Enforces: discount depth, margin floor, save-offer
cooldown (eligibility), pause length, and the human-in-the-loop gate for
consequential/irreversible tools (GDPR Art. 22, calibrated autonomy).

`authorize` returns a verdict the runtime obeys verbatim:
  action ∈ {ok, capped, rejected, needs_human}
"""

from __future__ import annotations

import config

# Save offers are subject to the cooldown-eligibility rule.
_SAVE_OFFERS = frozenset({"offer_discount", "offer_pause"})


def _cooldown_active(sub: dict) -> bool:
    days = sub.get("last_save_offer_days")
    return days is not None and days < config.SAVE_OFFER_COOLDOWN_DAYS


def authorize(tool_name: str, args: dict, sub: dict) -> dict:
    """Dispose a proposed action. `sub` is the customer's subscription row.

    Returns {action, allowed, reason, args} where `args` may be modified
    (e.g. a capped pct/months).
    """
    # Consequential / irreversible → human confirmation (Art. 22).
    if tool_name in config.CONSEQUENTIAL_TOOLS:
        return {"action": "needs_human", "allowed": False,
                "reason": f"{tool_name} has a significant effect; human approval required.",
                "args": args}

    # Save offers: eligibility (cooldown) gate first.
    if tool_name in _SAVE_OFFERS and _cooldown_active(sub):
        return {"action": "rejected", "allowed": False,
                "reason": (f"Customer received a save offer {sub['last_save_offer_days']} days ago; "
                           f"no further save offer within {config.SAVE_OFFER_COOLDOWN_DAYS} days."),
                "args": args}

    if tool_name == "offer_discount":
        pct = float(args.get("pct", 0))
        capped = min(pct, config.MAX_DISCOUNT_PCT)
        post_price = float(sub.get("price", 0)) * (1 - capped / 100)
        if post_price < config.MARGIN_FLOOR_USD:
            return {"action": "rejected", "allowed": False,
                    "reason": f"Discount would drop price below the ${config.MARGIN_FLOOR_USD:.0f} margin floor.",
                    "args": args}
        if capped < pct:
            return {"action": "capped", "allowed": True,
                    "reason": f"Discount capped from {pct:.0f}% to the {config.MAX_DISCOUNT_PCT}% ceiling.",
                    "args": {"pct": capped}}
        return {"action": "ok", "allowed": True, "reason": "Within policy.", "args": {"pct": capped}}

    if tool_name == "offer_pause":
        months = int(args.get("months", 0))
        capped = max(1, min(months, config.MAX_PAUSE_MONTHS))
        if capped < months:
            return {"action": "capped", "allowed": True,
                    "reason": f"Pause capped from {months} to {config.MAX_PAUSE_MONTHS} months.",
                    "args": {"months": capped}}
        return {"action": "ok", "allowed": True, "reason": "Within policy.", "args": {"months": capped}}

    if tool_name == "escalate_to_human":
        return {"action": "ok", "allowed": True, "reason": "Escalation is always permitted.", "args": args}

    # Unknown action tool → fail closed.
    return {"action": "rejected", "allowed": False, "reason": f"Unknown action tool {tool_name}.", "args": args}
