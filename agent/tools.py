"""Deterministic agent tools — the functions the model may call (plan §3a).

Each tool operates ONLY on the authenticated customer's own records (tool
sandboxing, plan §4). Read tools return account facts; action tools *propose*
an effect that the policy layer (policy.py) must authorize before it commits.

Phase 1 fills these in. Read tools are simple keel.db reads; action tools
record intent and return a structured result the runtime loop can act on. The
OpenAI tool JSON schemas are derived from these signatures in runtime.py.
"""

from __future__ import annotations

_PHASE = "Phase 1"


def get_customer(customer_id: int) -> dict:
    raise NotImplementedError(f"tools.get_customer — {_PHASE}")


def get_subscription(customer_id: int) -> dict:
    raise NotImplementedError(f"tools.get_subscription — {_PHASE}")


def get_usage(customer_id: int) -> dict:
    raise NotImplementedError(f"tools.get_usage — {_PHASE}")


def offer_discount(customer_id: int, pct: float) -> dict:
    """Propose a discount; policy layer caps at config.MAX_DISCOUNT_PCT."""
    raise NotImplementedError(f"tools.offer_discount — {_PHASE}")


def offer_pause(customer_id: int, months: int) -> dict:
    """Propose a pause; policy layer caps at config.MAX_PAUSE_MONTHS."""
    raise NotImplementedError(f"tools.offer_pause — {_PHASE}")


def downgrade_plan(customer_id: int, plan: str) -> dict:
    """CONSEQUENTIAL — routes to human approval (config.CONSEQUENTIAL_TOOLS)."""
    raise NotImplementedError(f"tools.downgrade_plan — {_PHASE}")


def escalate_to_human(customer_id: int, reason: str) -> dict:
    """End the turn; outcome='escalated'."""
    raise NotImplementedError(f"tools.escalate_to_human — {_PHASE}")
