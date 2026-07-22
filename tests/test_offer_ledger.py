"""Tests for the offer ledger, the persisted eval envelope, and the safety gate —
the invariants the third review found untested. No real API calls.

These prove the architectural fixes hold as DATA invariants, not just in prose:
exact terms survive persistence, one offer is active at a time, a below-ceiling
presented offer is costed at the presented (not authorized) terms, and the
guardrail-health gate honours version + freshness.
"""

from __future__ import annotations

import pytest

import config
import db
import synth
from agent import offers, runtime, safety
from evals import run_evals


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    synth.generate(c)
    yield c
    c.close()


# --- offer ledger ----------------------------------------------------------
def test_multiple_offers_may_be_authorized_one_presented():
    led: list[offers.Offer] = []
    d = offers.authorize(led, "discount", {"pct": 10})
    p = offers.authorize(led, "pause", {"months": 3})
    # both remain authorized during a negotiation (the agent may explore both)
    assert [o.state for o in led] == ["authorized", "authorized"]
    # presenting one supersedes any OTHER presented offer → at most one presented
    offers.present(led, d, {"pct": 10})
    offers.present(led, p, {"months": 3})
    assert p.state == "presented" and d.state == "superseded"
    assert offers.offer_of_kind(led, "pause") is p


def test_offer_summary_prefers_presented_then_accepted():
    led: list[offers.Offer] = []
    o = offers.authorize(led, "discount", {"pct": 20})
    o.state, o.presented_terms = "presented", {"pct": 15}      # presented LESS than the 20 ceiling
    assert offers.offer_summary(led) == "15% discount"          # not 20%
    offers.mark_accepted(led)
    assert offers.accepted(led) is o and offers.offer_summary(led) == "15% discount"


def test_rejection_transitions_presented_to_rejected():
    led: list[offers.Offer] = []
    o = offers.authorize(led, "discount", {"pct": 20})
    offers.present(led, o, {"pct": 20})
    offers.mark_rejected(led)
    assert o.state == "rejected"
    # a rejected offer was still EXTENDED — it shows in the offer summary (cooldown/analytics)
    assert offers.extended(led) is o and offers.offer_summary(led) == "20% discount"
    # …but it is no longer 'presented' (a faithful state machine)
    assert offers.presented(led) is None


def test_terms_within_ceiling():
    assert offers.terms_within({"pct": 10}, {"pct": 20}, "discount")
    assert not offers.terms_within({"pct": 25}, {"pct": 20}, "discount")
    assert offers.terms_within({"months": 2}, {"months": 3}, "pause")
    assert not offers.terms_within({"months": 4}, {"months": 3}, "pause")


# --- eval envelope survives persistence (the H1 regression) ----------------
def test_authorized_terms_survive_persistence_and_reload(conn):
    rec = runtime._new_rec()
    # authorize 10% (a policy cap of a larger ask), present exactly 10%
    o = offers.authorize(rec["offers"], "discount", {"pct": 10})
    o.state, o.presented_terms = "presented", {"pct": 10}
    rec["tool_facts"].append({"tool": "get_subscription", "call_id": "c1",
                              "result": {"plan": "Pro", "price": 99.0}})
    rec["policy_decisions"].append({"tool": "offer_discount", "action": "ok",
                                    "args": {"pct": 10}, "reason": "within policy"})
    record = {"customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": "hi"}],
              "disposition": {"outcome": "saved", "offer_made": "10% discount"}, "outcome": "saved",
              "offer_made": runtime._offer_made(rec), "evidence": runtime._evidence(rec),
              "guardrail_events": [], "audit": []}
    cid = runtime.persist_conversation(conn, record)

    convo = run_evals.build_judge_input(conn, cid)
    assert convo["offers"][0]["authorized_terms"] == {"pct": 10}   # EXACT 10%, not a lossy tool:action
    assert convo["offers"][0]["presented_terms"] == {"pct": 10}
    assert convo["tool_facts"][0]["result"]["price"] == 99.0        # tool facts reach the judge


# --- safety gate: version + freshness (the M3 regression) ------------------
def test_missing_health_is_advisory_not_gating(conn):
    st = safety.program_state(conn)
    assert st["metrics"]["guardrail_catch_rate"] is None
    assert any("not validated" in a for a in st["advisories"])
    assert st["healthy"] is True                                   # console usable out of the box


def test_stale_version_health_forces_safe_mode(conn):
    db.record_health(conn, "guardrail_catch_rate", 1.0, "old", version="0")  # not the current version
    st = safety.program_state(conn)
    assert st["healthy"] is False and st["mode"] == "safe"
    assert any("version" in r for r in st["reasons"])


def test_below_floor_health_forces_safe_mode(conn):
    db.record_health(conn, "guardrail_catch_rate", 0.50, "bad", version=config.GUARDRAIL_VERSION)
    st = safety.program_state(conn)
    assert st["healthy"] is False and any("catch rate" in r for r in st["reasons"])


def test_current_healthy_signal_stays_normal(conn):
    db.record_health(conn, "guardrail_catch_rate", 1.0, "good", version=config.GUARDRAIL_VERSION)
    st = safety.program_state(conn)
    assert st["healthy"] is True and st["mode"] == "normal"


# --- batch honours the caller DB; in-memory is rejected, not silently wrong --
def test_batch_rejects_in_memory_connection():
    import batch
    mem = db.connect(":memory:")
    db.init_db(mem)
    with pytest.raises(ValueError):
        batch.run_batch(mem, [{"id": 1, "customer_id": 1, "opening_message": "hi"}])
    mem.close()


# --- persistence defensively redacts EVERY role (terminal sim reply included) -
def test_persistence_redacts_all_roles(conn):
    record = {"customer_id": 1, "scenario_id": None,
              "transcript": [{"role": "user", "content": "my card is 4111 1111 1111 1111"},
                             {"role": "assistant", "content": "noted"}],
              "disposition": {"outcome": "lost", "offer_made": None}, "outcome": "lost",
              "offer_made": None, "evidence": {}, "guardrail_events": [], "audit": []}
    cid = runtime.persist_conversation(conn, record)
    stored = conn.execute("SELECT transcript_json FROM conversations WHERE id=?", (cid,)).fetchone()[0]
    assert "4111" not in stored  # user turn scrubbed even without an upstream guarantee


# --- two offer tool calls both authorize; presenting enforces one (H2) -------
def test_two_offer_calls_authorize_both(conn):
    rec = runtime._new_rec()
    sub = {"plan": "Pro", "price": 99.0, "last_save_offer_days": None}
    runtime._resolve_call("offer_discount", {"pct": 10}, 1, sub, conn, rec)
    runtime._resolve_call("offer_pause", {"months": 2}, 1, sub, conn, rec)
    # both authorized (the agent explored both); neither presented yet
    assert {o.kind for o in rec["offers"]} == {"discount", "pause"}
    assert all(o.state == "authorized" for o in rec["offers"])
    # a contract committing the pause presents ONLY the pause
    runtime._apply_contract(
        {"commitments": [{"kind": "pause", "pct": None, "months": 2}], "account_claims": []}, rec)
    presented = [o for o in rec["offers"] if o.state == "presented"]
    assert len(presented) == 1 and presented[0].kind == "pause"
