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
from agent import guardrails, runtime


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
        rec["offer_made"] = "1-month pause"
        input_list.append({"role": "assistant", "content": "How about a 1-month pause?"})
        return "How about a 1-month pause?"

    monkeypatch.setattr(runtime, "_agent_turn", fake_agent)
    monkeypatch.setattr(runtime, "_disposition",
                        lambda transcript, scenario, rec, outcome, accepted: {
                            "intent": "cancel", "churn_reason": "price", "offer_made": rec["offer_made"],
                            "offer_accepted": accepted, "outcome": outcome, "confidence": 0.8})

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
