"""FastAPI endpoint tests via TestClient (Phase: console). No real API calls.

Seeds a temp DB, forces the scope classifier in-scope, and monkeypatches the
agent turn + disposition — so this exercises the server plumbing (session state,
the background-thread + polling turn, persistence, error paths) deterministically.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import config
import db
import synth
from agent import guardrails, offers, runtime


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = str(tmp_path / "s.db")
    monkeypatch.setattr(config, "DB_PATH", p)
    c = db.connect(p)
    synth.generate(c)
    c.close()

    monkeypatch.setattr(guardrails, "check_scope", lambda t: {"in_scope": True, "reason": "t"})

    def fake_agent(input_list, cid, sub, conn, rec, system=runtime.SYSTEM, on_step=None):
        if on_step:
            on_step({"kind": "tool", "text": "get_subscription → Pro"})
        # Mirror a real turn: authorize + present a 1-month pause in the ledger.
        o = offers.authorize(rec["offers"], "pause", {"months": 1})
        o.state, o.presented_terms = "presented", {"months": 1}
        input_list.append({"role": "assistant", "content": "How about a 1-month pause?"})
        return "How about a 1-month pause?"

    monkeypatch.setattr(runtime, "_agent_turn", fake_agent)
    monkeypatch.setattr(runtime, "_disposition",
                        lambda transcript, scenario, rec, outcome, accepted: {
                            "intent": "cancel", "churn_reason": "price", "offer_made": runtime._offer_made(rec),
                            "offer_accepted": accepted, "outcome": outcome, "confidence": 0.8})
    monkeypatch.setattr(runtime, "_grade_and_store", lambda *a: None)  # no judge API in tests

    import server
    return TestClient(server.app)


def _drain_turn(client, turn_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/turn/{turn_id}").json()
        if st["done"]:
            return st
        time.sleep(0.03)
    raise AssertionError("turn did not complete in time")


def test_health_and_customers(client):
    assert client.get("/api/health").json()["ok"] is True
    cs = client.get("/api/customers").json()
    assert len(cs) > 0 and "plan" in cs[0]


def test_full_chat_flow(client):
    start = client.post("/api/chat/start", json={"customer_id": 1}).json()
    assert start["session_id"] and start["disclosure"]

    turn = client.post("/api/chat/turn", json={"session_id": start["session_id"], "message": "Too expensive."})
    assert turn.status_code == 200
    st = _drain_turn(client, turn.json()["turn_id"])
    assert st["error"] is None
    assert st["result"]["reply"] == "How about a 1-month pause?"
    assert any(s["kind"] == "tool" for s in st["steps"])  # the legibility trace populated

    res = client.post("/api/chat/resolve", json={"session_id": start["session_id"], "outcome": "saved"}).json()
    assert res["outcome"] == "saved" and res["conversation_id"]

    # it now shows up in the explorer and the detail is readable
    convs = client.get("/api/conversations").json()
    assert any(c["id"] == res["conversation_id"] for c in convs)
    detail = client.get(f"/api/conversations/{res['conversation_id']}").json()
    assert detail["outcome"] == "saved" and len(detail["transcript"]) >= 2


def test_metrics_shape(client):
    m = client.get("/api/metrics").json()
    for k in ("conversations", "save_rate", "madj_save_rate", "compliance", "guardrail_counts"):
        assert k in m


def test_error_paths(client):
    assert client.get("/api/turn/nope").status_code == 404
    assert client.post("/api/chat/turn", json={"session_id": "nope", "message": "hi"}).status_code == 404
    assert client.post("/api/chat/resolve", json={"session_id": "nope", "outcome": "saved"}).status_code == 404
    start = client.post("/api/chat/start", json={"customer_id": 1}).json()
    assert client.post("/api/chat/resolve", json={"session_id": start["session_id"], "outcome": "bogus"}).status_code == 422
    assert client.post("/api/chat/start", json={"customer_id": 99999}).status_code == 404


def test_cannot_falsify_saved_without_offer(client):
    # start a session and immediately resolve as 'saved' with no offer ever made
    start = client.post("/api/chat/start", json={"customer_id": 1}).json()
    r = client.post("/api/chat/resolve", json={"session_id": start["session_id"], "outcome": "saved"})
    assert r.status_code == 422  # server rejects the falsified outcome


def test_busy_session_rejects_overlapping_turn_and_resolve(client):
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[sid]["_busy"] = True  # simulate a turn in flight
    assert client.post("/api/chat/turn", json={"session_id": sid, "message": "hi"}).status_code == 409
    assert client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"}).status_code == 409


def test_escalated_session_rejects_further_turns(client):
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[sid]["outcome"] = "escalated"  # simulate a hand-off
    r = client.post("/api/chat/turn", json={"session_id": sid, "message": "give me 90% off"})
    assert r.status_code == 409  # no more agent turns after escalation


def test_resolve_connection_failure_clears_claim(client, monkeypatch):
    # If opening the DB connection fails during resolve, the _resolving claim must
    # be cleared (inside try/finally) — the session must not stick permanently.
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    monkeypatch.setattr(server.db, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):  # surfaced, not swallowed
        client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"})
    assert server.SESSIONS[sid].get("_resolving") is False  # claim released in finally, not stuck


def test_duplicate_resolve_claim_rejected(client):
    # A resolve in flight (the atomic _resolving claim) rejects a second resolve,
    # so two concurrent requests can't both run resolve_session.
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[sid]["_resolving"] = True  # simulate a resolve already claimed
    assert client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"}).status_code == 409


def test_worker_exception_surfaces_not_hangs(client, monkeypatch):
    from agent import runtime
    monkeypatch.setattr(runtime, "live_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    tid = client.post("/api/chat/turn", json={"session_id": sid, "message": "hi"}).json()["turn_id"]
    st = _drain_turn(client, tid)
    assert st["error"] and "boom" in st["error"]  # surfaced, not hung
    import server
    assert server.SESSIONS[sid]["_busy"] is False  # busy flag cleared for the next turn
