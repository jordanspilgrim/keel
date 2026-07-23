"""Keel Console — FastAPI server (the web Channel adapter).

Exposes the agent as a live, human-driven conversation plus a read-only explorer
over the DB. Two integration seams are explicit here:

  • Channel  — this web app is the first Channel adapter (browser ⇄ /api/chat).
               A new channel (Intercom, Zendesk, Twilio) is another adapter that
               maps its payloads onto new_session / live_turn; the agent core is
               untouched.
  • Backend  — agent/tools.py is the Backend seam (synthetic SQLite here). A real
               integration implements those tool functions against a real CRM /
               billing system; nothing else changes.

Robustness rules (the build goal is bug-free):
  • No shared SQLite connection — every request/worker opens its own and closes
    it (SQLite connections are thread-bound).
  • Live turns run in a background thread and are polled, so a slow model call
    never blocks the event loop and there is no long-lived connection to leak.
  • A worker exception is captured and returned as an error, never a hang.
  • A per-session busy-guard rejects overlapping turns.

Run:  uvicorn server:app --port 8500     (or via .claude/launch.json → "console")
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
from agent import runtime, safety
from dashboard import export
from evals import judge

app = FastAPI(title="Keel Console")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONSOLE = os.path.join(_HERE, "console", "index.html")

# In-memory state (single-tenant demo; reset on restart).
SESSIONS: dict[str, dict] = {}
TURNS: dict[str, dict] = {}
_LOCK = threading.Lock()
_SESSION_TTL_S = 3600  # abandoned (unresolved, idle) sessions are evicted after this


class StartReq(BaseModel):
    customer_id: int


class TurnReq(BaseModel):
    session_id: str
    message: str


class ResolveReq(BaseModel):
    session_id: str
    outcome: str = "lost"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(_CONSOLE)


# ---------------------------------------------------------------------------
# Chat (the live Channel)
# ---------------------------------------------------------------------------
@app.get("/api/customers")
def customers():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT c.id, c.name, c.segment, c.tenure_months, s.plan, s.price, "
            "s.last_save_offer_days FROM customers c JOIN subscriptions s ON s.customer_id = c.id "
            "ORDER BY c.id LIMIT 40"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/chat/start")
def chat_start(req: StartReq):
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM customers WHERE id=?", (req.customer_id,)).fetchone():
            raise HTTPException(404, "customer not found")
        session = runtime.new_session(req.customer_id, conn)
    finally:
        conn.close()
    sid = uuid.uuid4().hex
    with _LOCK:
        session["_last_active"] = time.time()
        SESSIONS[sid] = session
        _evict()  # sweep abandoned sessions on EVERY start, not only on a later turn (M6)
    return {"session_id": sid, "disclosure": session["disclosure"], "customer": session["customer"]}


def _evict() -> None:
    """Bound memory. Called under _LOCK. Three sweeps: cap completed turn states, cap
    resolved sessions, and TTL-evict ABANDONED sessions — unresolved conversations a
    user walked away from mid-flow, which otherwise accumulate forever. A busy session
    (a turn in progress) is never evicted."""
    done = [t for t, s in TURNS.items() if s["done"]]
    for t in done[:-100]:
        TURNS.pop(t, None)
    resolved = [sid for sid, s in SESSIONS.items() if s.get("resolved")]
    for sid in resolved[:-50]:
        SESSIONS.pop(sid, None)
    now = time.time()
    stale = [sid for sid, s in SESSIONS.items()
             if not s.get("resolved") and not s.get("_busy")
             and now - s.get("_last_active", now) > _SESSION_TTL_S]
    for sid in stale:
        SESSIONS.pop(sid, None)


@app.post("/api/chat/turn")
def chat_turn(req: TurnReq):
    if not req.message.strip():
        raise HTTPException(422, "message is empty")
    # Atomic admission: check session state AND claim the busy flag under one lock,
    # so two concurrent turns can't both see the session idle.
    with _LOCK:
        session = SESSIONS.get(req.session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        if session.get("resolved"):
            raise HTTPException(400, "this conversation has already ended")
        if session.get("outcome") == "escalated":
            raise HTTPException(409, "this conversation was escalated to a human — no more agent turns")
        if session.get("_busy"):
            raise HTTPException(409, "a turn is already in progress")
        session["_busy"] = True
        session["_last_active"] = time.time()  # keep an active conversation out of the TTL sweep
        tid = uuid.uuid4().hex
        turn_state = {"steps": [], "done": False, "result": None, "error": None}
        TURNS[tid] = turn_state
        _evict()

    def _publish_step(s):
        # Append under the SAME lock turn_status reads with, so a concurrent poll never
        # observes the steps list mid-mutation.
        with _LOCK:
            turn_state["steps"].append(s)

    def worker():
        conn = None
        result, error = None, None
        try:
            conn = db.connect()  # inside try — a connect failure surfaces, never hangs
            result = runtime.live_turn(session, req.message, conn, on_step=_publish_step)
        except Exception as e:  # never hang — surface the error
            error = f"{type(e).__name__}: {e}"
        finally:
            if conn is not None:
                conn.close()
            # Publish the terminal state under the lock so a concurrent poll sees a
            # consistent snapshot; 'done' is set last so it implies not busy.
            with _LOCK:
                turn_state["result"] = result
                turn_state["error"] = error
                session["_busy"] = False
                turn_state["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return {"turn_id": tid}


@app.get("/api/turn/{turn_id}")
def turn_status(turn_id: str):
    # Copy the whole turn state while holding the SAME lock the worker mutates
    # under, so the response is a consistent snapshot (not a torn read).
    with _LOCK:
        ts = TURNS.get(turn_id)
        if ts is None:
            raise HTTPException(404, "turn not found")
        return {"steps": list(ts["steps"]), "done": ts["done"],
                "result": ts["result"], "error": ts["error"]}


def _durable_resolution(session_id: str) -> dict | None:
    """The durably-committed resolution for a key, or None — the source of truth that
    survives a process restart (when the in-memory SESSIONS map is empty)."""
    conn = db.connect()
    try:
        return runtime._load_resolved_record(conn, session_id)
    finally:
        conn.close()


@app.post("/api/chat/resolve")
def chat_resolve(req: ResolveReq):
    with _LOCK:
        session = SESSIONS.get(req.session_id)
    if session is None:
        # After a restart the in-memory session is gone, but this key may have durably
        # committed — honor the documented cross-restart retry by consulting the DB
        # before giving up, instead of 404-ing before the durable-key lookup.
        existing = _durable_resolution(req.session_id)
        if existing is not None:
            return {"conversation_id": existing["conversation_id"], "outcome": existing["outcome"],
                    "disposition": existing["disposition"]}
        raise HTTPException(404, "session not found, and no prior resolution is recorded for it")
    with _LOCK:
        if session.get("_busy"):
            raise HTTPException(409, "a turn is still in progress")
        # Idempotent retry: an already-resolved session returns its existing record
        # rather than a 409 — resolving is safe to repeat (same outcome, same row).
        if session.get("resolved"):
            rec = session.get("_record") or {}
            return {"conversation_id": rec.get("conversation_id"), "outcome": rec.get("outcome"),
                    "disposition": rec.get("disposition")}
        if session.get("_resolving"):
            raise HTTPException(409, "this conversation is already being resolved")
        # Claim the resolve under the lock so a second concurrent request can't
        # also pass the checks above before resolve_session flips 'resolved'.
        session["_resolving"] = True
    conn = None
    try:
        conn = db.connect()  # inside try — a connect failure clears the claim, never sticks
        # Pass the session id as the durable resolution key: a retry (even after a
        # restart, on a fresh session object) returns the DB-persisted record.
        record = runtime.resolve_session(session, req.outcome, conn, resolution_key=req.session_id)
    except ValueError as e:  # invalid transition (e.g. 'saved' with no accepted offer)
        raise HTTPException(422, str(e))
    finally:
        if conn is not None:
            conn.close()
        with _LOCK:
            session["_resolving"] = False  # release the claim (resolved stays set on success)
    return {"conversation_id": record["conversation_id"], "outcome": record["outcome"],
            "disposition": record["disposition"]}


# ---------------------------------------------------------------------------
# Explorer (read-only over the DB)
# ---------------------------------------------------------------------------
@app.get("/api/metrics")
def metrics():
    conn = db.connect()
    try:
        m = export.conversation_metrics(conn)
        # Eval + compliance use the SAME canonical definitions the dashboard exports
        # (export.eval_metrics / export.compliance_coverage) — one source of truth.
        evalm = export.eval_metrics(conn)
        g = dict(conn.execute("SELECT type, count(*) FROM guardrail_events GROUP BY type").fetchall())
        return {
            "conversations": m["n"],
            "save_rate": m["save_rate"],
            "madj_save_rate": m["madj_save_rate"],
            "eval_pass_rate": evalm["eval_pass_rate"],
            "eval_coverage": evalm["eval_coverage"],
            "compliance": export.compliance_coverage(conn),
            "guardrail_counts": g,
            "guardrail_total": sum(g.values()),
            "safety": safety.program_state(conn),
        }
    finally:
        conn.close()


@app.get("/api/conversations")
def conversations():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, outcome, offer_made, disposition_json FROM conversations ORDER BY id DESC LIMIT 200"
        ).fetchall()
        out = []
        for r in rows:
            disp = json.loads(r["disposition_json"])
            verdict = conn.execute(
                "SELECT verdict FROM evals WHERE conversation_id=? AND rubric_version=?",
                (r["id"], judge.EVAL_SPEC_VERSION)).fetchone()
            gcount = conn.execute("SELECT count(*) FROM guardrail_events WHERE conversation_id=?", (r["id"],)).fetchone()[0]
            out.append({"id": r["id"], "outcome": r["outcome"], "offer_made": r["offer_made"],
                        "reason": disp.get("churn_reason"), "eval": verdict["verdict"] if verdict else None,
                        "guardrails": gcount})
        return out
    finally:
        conn.close()


@app.get("/api/conversations/{cid}")
def conversation(cid: int):
    conn = db.connect()
    try:
        c = conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if c is None:
            raise HTTPException(404, "conversation not found")
        e = conn.execute(
            "SELECT scores_json, verdict, rationale, fairness_flag FROM evals WHERE conversation_id=? AND rubric_version=?",
            (cid, judge.EVAL_SPEC_VERSION)).fetchone()
        g = conn.execute("SELECT type, action, detail FROM guardrail_events WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
        a = conn.execute("SELECT actor, decision, reason FROM audit_log WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
        eval_obj = None
        if e is not None:
            eval_obj = {"verdict": e["verdict"], "rationale": e["rationale"],
                        "fairness_flag": bool(e["fairness_flag"]), "scores": json.loads(e["scores_json"])}
        return {"id": c["id"], "outcome": c["outcome"], "offer_made": c["offer_made"],
                "transcript": json.loads(c["transcript_json"]),
                "disposition": json.loads(c["disposition_json"]),
                "eval": eval_obj, "guardrail_events": [dict(x) for x in g], "audit": [dict(x) for x in a]}
    finally:
        conn.close()


@app.get("/api/health")
def health():
    return {"ok": True}
