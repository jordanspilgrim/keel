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
    monkeypatch.setattr(guardrails, "classify_injection", lambda t: False)  # M4: no real API in tests

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
    # The customer-decision classifier is a MODEL call and now runs on EVERY decision turn
    # (an explicit `decision` no longer bypasses it — the customer's words outrank the
    # button), so it must be stubbed here or the offline suite makes a real, billed call.
    monkeypatch.setattr(runtime, "classify_customer_decision",
                        lambda message, offer_summary: "accept" if any(
                            w in message.lower() for w in ("yes", "accept", "i'll take", "ok")) else "continue")

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

    # the customer accepts via the explicit decision (the Accept button) — earns the save (H2)
    acc = client.post("/api/chat/turn",
                      json={"session_id": start["session_id"], "message": "yes, I'll take it", "decision": "accept"})
    _drain_turn(client, acc.json()["turn_id"])

    res = client.post("/api/chat/resolve", json={"session_id": start["session_id"]}).json()  # derived
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


def test_two_threads_racing_resolve_only_one_wins(client):
    # A REAL race (not a pre-set flag): fire two resolves at the same session
    # concurrently; the atomic _resolving claim must let exactly one through.
    import threading
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    results, barrier = [], threading.Barrier(2)

    def go():
        barrier.wait()  # line both threads up so they truly contend
        results.append(client.post("/api/chat/resolve",
                                   json={"session_id": sid, "outcome": "lost"}).status_code)

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # one 200 (resolved), one 409/400 (rejected) — never two successes
    assert sorted(results)[0] == 200 and results.count(200) == 1
    assert results.count(200) + sum(1 for r in results if r in (400, 409)) == 2


def test_duplicate_resolve_claim_rejected(client):
    # A resolve in flight (the atomic _resolving claim) rejects a second resolve,
    # so two concurrent requests can't both run resolve_session.
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[sid]["_resolving"] = True  # simulate a resolve already claimed
    assert client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"}).status_code == 409


def test_abandoned_sessions_are_ttl_evicted_by_a_new_start(client):
    """M6/L2: an unresolved session a user walked away from is evicted once idle past the
    TTL — and the sweep runs on a NEW /chat/start (page refreshes), not only on a later
    turn. A busy session (turn in flight) is spared."""
    import server
    abandoned = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    busy = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[abandoned]["_last_active"] = time.time() - server._SESSION_TTL_S - 1
    server.SESSIONS[busy]["_last_active"] = time.time() - server._SESSION_TTL_S - 1
    server.SESSIONS[busy]["_busy"] = True
    # simply starting another session triggers the sweep (no turn required)
    client.post("/api/chat/start", json={"customer_id": 1})
    assert abandoned not in server.SESSIONS       # abandoned + idle → evicted on start
    assert busy in server.SESSIONS                # a turn in flight is never evicted


def test_resolve_retry_across_restart_returns_record(client):
    """M1: after a process restart (in-memory SESSIONS lost), retrying a resolve with the
    same session id consults the durable resolution key instead of 404-ing."""
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    r1 = client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"})
    assert r1.status_code == 200
    with server._LOCK:
        server.SESSIONS.clear()  # simulate a restart — in-memory sessions are gone
    r2 = client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"})
    assert r2.status_code == 200 and r2.json()["conversation_id"] == r1.json()["conversation_id"]
    # a never-seen key is still an honest 404
    assert client.post("/api/chat/resolve", json={"session_id": "never", "outcome": "lost"}).status_code == 404


def test_resolve_retry_returns_record_not_409(client):
    """L2/M1: a second resolve of an already-resolved session returns its record (200),
    not a 409 — resolving is idempotent from the client's view."""
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    r1 = client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"})
    assert r1.status_code == 200
    r2 = client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"})
    assert r2.status_code == 200  # not 409
    assert r2.json()["conversation_id"] == r1.json()["conversation_id"]


def test_worker_exception_surfaces_not_hangs(client, monkeypatch):
    from agent import runtime
    monkeypatch.setattr(runtime, "live_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    tid = client.post("/api/chat/turn", json={"session_id": sid, "message": "hi"}).json()["turn_id"]
    st = _drain_turn(client, tid)
    assert st["error"] and "boom" in st["error"]  # surfaced, not hung
    import server
    assert server.SESSIONS[sid]["_busy"] is False  # busy flag cleared for the next turn


# --- R13-D7: controls that were shipped with zero mutation signal -----------------
def test_turn_is_refused_while_a_resolve_is_in_flight(client):
    """The headline race of e8b29b9. chat_resolve claims _resolving, releases _LOCK, then
    spends a multi-second _disposition call outside any guard — a turn admitted in that
    window races the finalize. The guard shipped with NO test: reverting it left the suite
    fully green."""
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[sid]["_resolving"] = True          # simulate the mid-resolve window
    r = client.post("/api/chat/turn", json={"session_id": sid, "message": "yes I accept"})
    assert r.status_code == 409, r.text
    assert "finalized" in r.json()["detail"]


def test_a_session_mid_resolve_is_not_ttl_evicted(client):
    """Same guard, second half: a >TTL-idle session finalized via End could be swept out
    from under its own resolve, making both the resolve and its retry unrecoverable."""
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    s = server.SESSIONS[sid]
    s["_resolving"] = True
    s["_last_active"] = 0                              # ancient
    server._evict()
    assert sid in server.SESSIONS, "a session being finalized must never be evicted"


def test_oversized_message_is_refused(client):
    """Screening is regex-heavy and holds the GIL; an unbounded message let one caller
    stall every other session."""
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    r = client.post("/api/chat/turn", json={"session_id": sid, "message": "x" * 5000})
    assert r.status_code == 413


def test_out_of_range_integer_is_a_client_error_not_a_500(client):
    """An out-of-INT64 id reached sqlite3 and surfaced as a 500 — a malformed request, not
    a server fault."""
    r = client.post("/api/chat/start", json={"customer_id": 10**19})
    assert r.status_code == 422, r.status_code


def test_resolve_retry_asserting_a_different_outcome_is_refused(client, monkeypatch):
    """RT14: the anti-manufacture cross-check lives inside resolve_session, which a retry
    never reaches — so the first call was validated and every retry was not."""
    import server
    sid = client.post("/api/chat/start", json={"customer_id": 1}).json()["session_id"]
    server.SESSIONS[sid]["resolved"] = True
    server.SESSIONS[sid]["_record"] = {"conversation_id": 7, "outcome": "lost", "disposition": {}}
    ok = client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "lost"})
    assert ok.status_code == 200
    bad = client.post("/api/chat/resolve", json={"session_id": sid, "outcome": "saved"})
    assert bad.status_code == 422 and "contradicts" in bad.json()["detail"]


def test_live_session_count_is_capped(client, monkeypatch):
    """'Bound memory' was enforced only by a 1-hour TTL, so a client could open sessions
    faster than they expire."""
    import server
    monkeypatch.setattr(server, "_MAX_LIVE_SESSIONS", 2)
    ids = [client.post("/api/chat/start", json={"customer_id": 1}) for _ in range(3)]
    assert [r.status_code for r in ids].count(503) >= 1, [r.status_code for r in ids]
