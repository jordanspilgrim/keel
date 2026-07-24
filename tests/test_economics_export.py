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
    # a zero save rate must not divide-by-zero or report a favorable number — cost/save is
    # UNDEFINED (None → 'N/A'), not $0 (L1)
    r = economics.compute(economics.Levers(save_rate=0.0))
    assert r["cost_per_save"] is None
    assert r["vendor_pnl"]["revenue"] == 0


def test_economics_cost_per_save_scales_inversely_with_save_rate():
    # cost/save = cost/conversation ÷ save_rate (both reported to 4 decimals)
    r = economics.compute(economics.Levers(save_rate=0.5))
    assert r["cost_per_conversation"] == pytest.approx(1.2835, abs=5e-4)
    assert r["cost_per_save"] == pytest.approx(1.2835 / 0.5, abs=5e-4)
    # a lower save rate makes each save strictly more expensive (2× at half the rate)
    worse = economics.compute(economics.Levers(save_rate=0.25))
    assert worse["cost_per_save"] == pytest.approx(2 * r["cost_per_save"], abs=1e-3)


def test_pause_margin_cost_is_a_fixed_goodwill_fraction():
    # 1-month pause on a $100 plan concedes exactly $7 of goodwill margin when accepted
    assert economics.margin_cost("1-month pause", 100.0, accepted=True) == 7.0
    assert economics.margin_cost("2-month pause", 100.0, accepted=True) == 14.0
    assert economics.margin_cost("1-month pause", 100.0, accepted=False) == 0.0


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


def test_economics_zero_save_boundary_is_undefined_not_favorable():
    """L1: with NO saves, cost-per-save and gross-margin-per-save are UNDEFINED (None → the
    CLI renders 'N/A'), not an impossible $0 / 100%. The default (save_rate > 0) is unaffected."""
    r0 = economics.compute(economics.Levers(save_rate=0.0))
    assert r0["cost_per_save"] is None and r0["gross_margin_per_save"] is None
    assert r0["vendor_pnl"]["revenue"] == 0  # zero saves earn no revenue
    d = economics.compute()
    assert d["cost_per_save"] is not None and d["gross_margin_per_save"] is not None  # default still numeric


def test_economics_rejects_out_of_range_inputs():
    """L1: a rate outside [0,1] or a negative money/token input is rejected, so the model can
    never return a nonsense number from a nonsense input."""
    for bad in (economics.Levers(save_rate=1.5), economics.Levers(save_rate=-0.1),
                economics.Levers(escalation_rate=2.0), economics.Levers(outcome_fee=-1.0),
                economics.Levers(conversations=-5), economics.Levers(eval_sampling=1.2)):
        with pytest.raises(ValueError):
            economics.compute(bad)


# --- dashboard/export math -------------------------------------------------
def _persist(conn, outcome, offer, phase="baseline", run="run-A"):
    from agent import runtime
    runtime.persist_conversation(conn, {
        "customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "assistant", "content": "hi"}],
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
    # one save, discounted 20% → margin-adjusted contribution is (1 - 0.20) for that
    # save, averaged over 2 conversations → 0.40 exactly
    assert m["madj_save_rate"] == pytest.approx((1 - 0.20) / 2, abs=1e-6)
    assert price == 499.0  # customer 1 is on the Enterprise plan (deterministic synth)


def test_price_snapshot_is_frozen_at_conversation_time(conn):
    """L1: the price used for historical margin is snapshotted at conversation time, so a
    later subscription price change cannot rewrite history. conversation_metrics reads
    the snapshot (COALESCE with current only for legacy rows)."""
    _persist(conn, "saved", "20% discount")
    row = conn.execute("SELECT price_at_conversation FROM conversations").fetchone()
    assert row["price_at_conversation"] == 499.0          # customer 1's price at persist
    before = export.conversation_metrics(conn, run_id="run-A", phase="baseline")["madj_save_rate"]
    conn.execute("UPDATE subscriptions SET price = 100 WHERE customer_id = 1")
    conn.commit()
    frozen = conn.execute("SELECT price_at_conversation FROM conversations").fetchone()["price_at_conversation"]
    assert frozen == 499.0                                 # snapshot unchanged by the price update
    after = export.conversation_metrics(conn, run_id="run-A", phase="baseline")["madj_save_rate"]
    assert after == before                                 # history did not move


def test_eval_metrics_and_compliance_zero_safe(conn):
    # no conversations → no division by zero; rates are 0.0, coverage 0.0
    em = export.eval_metrics(conn)
    assert em["eval_pass_rate"] == 0.0 and em["eval_coverage"] == 0.0
    assert export.compliance_coverage(conn) == 0.0


def test_eval_metrics_never_exceed_one_across_spec_versions(conn):
    """H2 regression: a retained history of grades under SUPERSEDED eval specs must not
    inflate the pass rate. With one conversation graded 'pass' under both an old spec
    and the current spec, the rate is 1.0 (current spec only), never 2.0."""
    from evals import judge
    _persist(conn, "saved", "20% discount")
    cid = conn.execute("SELECT id FROM conversations").fetchone()["id"]
    # a stale grade under a superseded spec + the current-spec grade (unique index
    # allows one row per (conversation, version), so both coexist)
    for ver in ("spec-OLDVERSION0", judge.EVAL_SPEC_VERSION):
        conn.execute("INSERT INTO evals (conversation_id, scores_json, verdict, rubric_version, created_at) "
                     "VALUES (?,?,?,?,?)", (cid, "{}", "pass", ver, "t"))
    conn.commit()
    em = export.eval_metrics(conn)
    assert em["eval_pass_rate"] == 1.0    # current-spec only — not 2.0
    assert em["eval_coverage"] == 1.0
    # the kill switch reads the same current-spec counts
    passes, graded = judge.current_spec_eval_counts(conn)
    assert passes == 1 and graded == 1
