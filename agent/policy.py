"""Deterministic policy / authorization layer (plan §4 — the most important).

The model *proposes* an action; this layer *disposes*. Pure Python, no LLM —
so it cannot be talked past. Enforces authorization limits (max discount depth,
margin floor, save-offer cooldown), eligibility, and the human-in-the-loop gate
for consequential/irreversible tools (GDPR Art. 22).

Phase 2 fills these in. Every decision writes an audit_log row and, on a block,
a guardrail_events row.
"""

from __future__ import annotations

import config


def authorize(tool_name: str, args: dict, customer_ctx: dict) -> dict:
    """Return {allowed: bool, action: 'ok'|'capped'|'rejected'|'needs_human',
    reason: str, args: dict}. Deterministic; the runtime obeys it verbatim.

    Rules (Phase 2):
      - tool in CONSEQUENTIAL_TOOLS         -> needs_human
      - offer_discount pct > MAX_DISCOUNT_PCT -> capped to the ceiling
      - resulting margin < MARGIN_FLOOR_USD -> rejected
      - save offer within SAVE_OFFER_COOLDOWN_DAYS -> rejected (ineligible)
      - offer_pause months > MAX_PAUSE_MONTHS -> capped
      - otherwise                           -> ok
    """
    raise NotImplementedError("policy.authorize — Phase 2")
