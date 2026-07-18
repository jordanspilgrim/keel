"""Unit tests for the deterministic policy layer (Phase 1/2). No API calls."""

from __future__ import annotations

import config
from agent import policy

ELIGIBLE = {"plan": "Pro", "price": 99.0, "status": "active", "last_save_offer_days": None}
COOLDOWN = {"plan": "Pro", "price": 99.0, "status": "active", "last_save_offer_days": 10}


def test_discount_capped_at_ceiling():
    v = policy.authorize("offer_discount", {"pct": 40}, ELIGIBLE)
    assert v["allowed"] and v["action"] == "capped"
    assert v["args"]["pct"] == config.MAX_DISCOUNT_PCT


def test_discount_within_limit_ok():
    v = policy.authorize("offer_discount", {"pct": 10}, ELIGIBLE)
    assert v["allowed"] and v["action"] == "ok" and v["args"]["pct"] == 10


def test_save_offer_rejected_during_cooldown():
    for tool, args in [("offer_discount", {"pct": 10}), ("offer_pause", {"months": 1})]:
        v = policy.authorize(tool, args, COOLDOWN)
        assert not v["allowed"] and v["action"] == "rejected"
        assert "cooldown" in v["reason"].lower() or "save offer" in v["reason"].lower()


def test_pause_capped():
    v = policy.authorize("offer_pause", {"months": 12}, ELIGIBLE)
    assert v["allowed"] and v["action"] == "capped"
    assert v["args"]["months"] == config.MAX_PAUSE_MONTHS


def test_consequential_tool_needs_human():
    v = policy.authorize("downgrade_plan", {"new_plan": "Basic"}, ELIGIBLE)
    assert not v["allowed"] and v["action"] == "needs_human"


def test_refund_denial_routes_to_human():
    v = policy.authorize("deny_refund", {"reason": "outside window"}, ELIGIBLE)
    assert not v["allowed"] and v["action"] == "needs_human"


def test_margin_floor_rejects_deep_discount_on_cheap_plan():
    cheap = {"plan": "Basic", "price": 6.0, "status": "active", "last_save_offer_days": None}
    v = policy.authorize("offer_discount", {"pct": 20}, cheap)
    # 6.0 * 0.8 = 4.8 < 5.0 floor → rejected
    assert not v["allowed"] and v["action"] == "rejected"


def test_escalation_always_allowed():
    v = policy.authorize("escalate_to_human", {"reason": "customer upset"}, COOLDOWN)
    assert v["allowed"] and v["action"] == "ok"
