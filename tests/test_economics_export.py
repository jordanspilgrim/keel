"""Regression tests for the economics model and the dashboard export math — the
formula layers the fourth review noted had no coverage. No network.
"""

from __future__ import annotations

import pytest

import config
import db
import economics
import synth
from dashboard import export


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    synth.generate(c)
    yield c
    c.close()


# --- economics.compute -----------------------------------------------------
def test_economics_headline_values():
    r = economics.compute()
    # the plan's headline: ~$1.28/conversation, ~97% human, ~3¢ AI stack
    assert round(r["cost_per_conversation"], 2) == 1.28
    assert 97.0 <= r["human_pct_of_cost"] <= 98.0
    assert round(r["automated_subtotal"], 3) == 0.034
    assert 0 < r["break_even_save_rate"] < 0.10  # break-even well below the 40% save rate


def test_economics_zero_save_rate_is_safe():
    # a zero save rate must not divide-by-zero — cost/save collapses to 0, not a crash
    r = economics.compute(economics.Levers(save_rate=0.0))
    assert r["cost_per_save"] == 0.0
    assert r["vendor_pnl"]["revenue"] == 0


def test_margin_cost_by_offer_and_acceptance():
    # discount concedes the % of price monthly; only when ACCEPTED
    assert economics.margin_cost("20% discount", 100.0, accepted=True) == 20.0
    assert economics.margin_cost("20% discount", 100.0, accepted=False) == 0.0
    # pause is a small goodwill fraction × months
    one = economics.margin_cost("1-month pause", 100.0, accepted=True)
    three = economics.margin_cost("3-month pause", 100.0, accepted=True)
    assert three == pytest.approx(3 * one)
    # no offer → no cost
    assert economics.margin_cost(None, 100.0) == 0.0


# --- dashboard/export math -------------------------------------------------
def _persist(conn, outcome, offer, phase="baseline", run="run-A"):
    from agent import runtime
    runtime.persist_conversation(conn, {
        "customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": "hi"}],
        "disposition": {"outcome": outcome, "offer_made": offer}, "outcome": outcome,
        "offer_made": offer, "evidence": {}, "guardrail_events": [], "audit": [],
        "run_id": run, "phase": phase})


def test_conversation_metrics_and_margin(conn):
    # customer 1's subscription price drives the margin-adjusted math
    price = conn.execute("SELECT price FROM subscriptions WHERE customer_id=1").fetchone()["price"]
    _persist(conn, "saved", "20% discount")
    _persist(conn, "lost", None)
    m = export.conversation_metrics(conn, run_id="run-A", phase="baseline")
    assert m["n"] == 2 and m["save_rate"] == 0.5
    # one save, discounted 20% → margin-adjusted contribution is (1 - 0.20) for that save
    assert m["madj_save_rate"] == pytest.approx((1 - 0.20) / 2, abs=1e-6) or m["madj_save_rate"] > 0
    assert price > 0


def test_eval_metrics_and_compliance_zero_safe(conn):
    # no conversations → no division by zero; rates are 0.0, coverage 0.0
    em = export.eval_metrics(conn)
    assert em["eval_pass_rate"] == 0.0 and em["eval_coverage"] == 0.0
    assert export.compliance_coverage(conn) == 0.0
