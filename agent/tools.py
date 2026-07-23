"""Deterministic agent tools — the functions the model may call (plan §3a).

Tools operate ONLY on the authenticated customer's own records: the runtime
injects `customer_id` into the call context, so the model can never name another
account (tool sandboxing, plan §4). Read tools return account facts directly;
action tools are routed through the policy layer (policy.py) by the runtime
before any effect is recorded — the model *proposes*, policy *disposes*.

This module owns two things: the OpenAI tool JSON schemas (TOOL_SCHEMAS) and the
deterministic read implementations. Action authorization lives in policy.py and
is orchestrated by runtime.py.
"""

from __future__ import annotations

import random

# --- OpenAI Responses-API function tool schemas (strict) -------------------
# customer_id is NOT a parameter: the runtime binds it from the authenticated
# context, so the model cannot request another customer's data.
def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOL_SCHEMAS = [
    {"type": "function", "name": "get_customer",
     "description": "Get the current customer's profile (name, segment, tenure, ARPU).",
     "parameters": _obj({}, [])},
    {"type": "function", "name": "get_subscription",
     "description": "Get the current customer's subscription (plan, price, status, save-offer recency).",
     "parameters": _obj({}, [])},
    {"type": "function", "name": "get_usage",
     "description": "Get the current customer's recent product-usage summary.",
     "parameters": _obj({}, [])},
    {"type": "function", "name": "offer_discount",
     "description": "Propose a percentage discount as a retention offer. Policy may cap or reject it.",
     "parameters": _obj({"pct": {"type": "integer", "description": "Discount percent (whole number), e.g. 15"}}, ["pct"])},
    {"type": "function", "name": "offer_pause",
     "description": "Propose pausing the subscription for N months as a retention offer. Policy may cap or reject it.",
     "parameters": _obj({"months": {"type": "integer", "description": "Months to pause, e.g. 1"}}, ["months"])},
    {"type": "function", "name": "downgrade_plan",
     "description": "Propose moving the customer to a cheaper plan. Consequential — requires human approval.",
     "parameters": _obj({"new_plan": {"type": "string", "description": "Target plan name"}}, ["new_plan"])},
    {"type": "function", "name": "deny_refund",
     "description": "Decline a refund the customer requested. Consequential (legal/financial effect) — requires human approval.",
     "parameters": _obj({"reason": {"type": "string"}}, ["reason"])},
    {"type": "function", "name": "escalate_to_human",
     "description": "Hand the conversation to a human agent. Use when uncertain or the customer needs a person.",
     "parameters": _obj({"reason": {"type": "string"}}, ["reason"])},
]

# Strict function-calling: the API validates arguments against each schema (required +
# additionalProperties:false) before the model can emit a call, so the deterministic
# policy layer receives well-typed args. The runtime ALSO validates defensively (see
# _safe_tool_args) — strict schemas are the first gate, not the only one.
for _t in TOOL_SCHEMAS:
    _t["strict"] = True

READ_TOOLS = frozenset({"get_customer", "get_subscription", "get_usage"})


# --- read implementations (direct DB reads, no side effects) ---------------
def get_customer(conn, customer_id: int) -> dict:
    row = conn.execute(
        "SELECT name, segment, tenure_months, arpu FROM customers WHERE id=?", (customer_id,)
    ).fetchone()
    return dict(row) if row else {"error": "not found"}


def get_subscription(conn, customer_id: int) -> dict:
    row = conn.execute(
        "SELECT plan, price, status, last_save_offer_days FROM subscriptions WHERE customer_id=?",
        (customer_id,),
    ).fetchone()
    return dict(row) if row else {"error": "not found"}


def get_usage(conn, customer_id: int) -> dict:
    """Synthetic usage, derived deterministically from the customer id + ARPU so
    it's stable across runs (no usage table — this is a mock backend)."""
    rng = random.Random(customer_id)
    sub = get_subscription(conn, customer_id)
    seats = {"Basic": 3, "Pro": 12, "Enterprise": 50}.get(sub.get("plan", "Basic"), 5)
    return {
        "logins_last_30d": rng.randint(0, 40),
        "active_seats": rng.randint(1, seats),
        "licensed_seats": seats,
        "feature_adoption_pct": rng.randint(10, 95),
    }


def read(conn, name: str, customer_id: int) -> dict:
    """Dispatch a read tool by name."""
    return {"get_customer": get_customer, "get_subscription": get_subscription,
            "get_usage": get_usage}[name](conn, customer_id)
