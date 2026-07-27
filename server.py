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
from fastapi.responses import FileResponse, JSONResponse
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
    decision: str | None = None  # optional Accept/Reject button (H2); else the reply is classified


class ResolveReq(BaseModel):
    session_id: str
    # Optional cross-check only — the outcome is DERIVED from the earned ledger state; the
    # server never manufactures a save from a caller-supplied value (H2).
    outcome: str | None = None


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
        _evict()  # reclaim resolved/abandoned sessions before testing the cap
        if len(SESSIONS) >= _MAX_LIVE_SESSIONS:
            # A 1-hour TTL alone does not bound anything: a client can open sessions faster
            # than they expire. Refuse rather than grow without limit.
            raise HTTPException(503, "too many concurrent live sessions — try again shortly")
        session["_session_id"] = sid  # so live_turn can durably key a routed cancellation
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
             if not s.get("resolved") and not s.get("_busy") and not s.get("_resolving")
             and now - s.get("_last_active", now) > _SESSION_TTL_S]
    for sid in stale:
        SESSIONS.pop(sid, None)


_MAX_MESSAGE_CHARS = 4000
# RT27: "bound memory" was enforced only by a 1-hour TTL, so nothing stopped a client
# opening sessions faster than they expire. A hard cap makes the claim true.
_MAX_LIVE_SESSIONS = 500


@app.exception_handler(OverflowError)
def _overflow_handler(request, exc):
    """RT26: an out-of-INT64 integer (e.g. customer_id 10**19) reached sqlite3 and raised
    OverflowError, surfacing as a 500. That is a malformed request, not a server fault."""
    return JSONResponse(status_code=422, content={"detail": "numeric value out of range"})


@app.post("/api/chat/turn")
def chat_turn(req: TurnReq):
    if not req.message.strip():
        raise HTTPException(422, "message is empty")
    # Bound the work a single request can cause. Screening is regex-heavy and holds the GIL,
    # so an unbounded message let one caller stall every other session. 4000 chars is far
    # above any real customer turn.
    if len(req.message) > _MAX_MESSAGE_CHARS:
        raise HTTPException(413, f"message exceeds {_MAX_MESSAGE_CHARS} characters")
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
        if session.get("outcome") in ("saved", "lost", "cancelled"):
            # A customer decision is TERMINAL (as in the batch path) — re-entering the agent
            # could otherwise overwrite an earned save with a contradictory outcome.
            # 'cancelled' was missing here while runtime enumerates all four terminals, so a
            # cancelled session was the ONE terminal /api/chat/turn still admitted — costing a
            # classifier call and two transcript entries per turn, unbounded. Recovery for a
            # swallowed finalize is /api/chat/resolve (finalize-only, still admitted), plus the
            # retry now in runtime's cancelled branch — not an open agent turn on a closed
            # conversation.
            raise HTTPException(409, f"this conversation is already closed as '{session['outcome']}' "
                                     f"— no more agent turns")
        if session.get("_busy"):
            raise HTTPException(409, "a turn is already in progress")
        if session.get("_resolving"):
            # The guard was one-directional: chat_resolve refused on _busy, but admission
            # never checked _resolving. chat_resolve claims the flag, RELEASES _LOCK, then
            # spends a multi-second _disposition call outside any guard — so a turn admitted
            # in that window raced the finalize. Reproduced: resolve derives 'lost' and flips
            # the presented offer to rejected, then an accept turn is admitted, finds no
            # presented offer, and re-runs as an ordinary turn — durable outcome 'lost',
            # zero fulfillment rows, for a customer who accepted.
            raise HTTPException(409, "this conversation is being finalized — no more agent turns")
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
            result = runtime.live_turn(session, req.message, conn, on_step=_publish_step,
                                       decision=req.decision)
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
            # The cross-check that stops a caller manufacturing an outcome runs inside
            # resolve_session, which a retry never reaches — so the FIRST call was validated
            # and every retry was not. A retry asserting a different outcome than the one
            # actually recorded is a contradiction, not an idempotent repeat.
            if req.outcome is not None and rec.get("outcome") is not None \
                    and req.outcome != rec.get("outcome"):
                raise HTTPException(422, f"asserted outcome '{req.outcome}' contradicts the "
                                         f"recorded outcome '{rec.get('outcome')}'")
            return {"conversation_id": rec.get("conversation_id"), "outcome": rec.get("outcome"),
                    "disposition": rec.get("disposition")}
        if session.get("_resolving"):
            raise HTTPException(409, "this conversation is already being resolved")
        # Claim the resolve under the lock so a second concurrent request can't
        # also pass the checks above before resolve_session flips 'resolved'.
        session["_resolving"] = True
        session["_last_active"] = time.time()  # resolving IS activity — a >TTL-idle session
        # finalized via End must not be evicted mid-resolve, which made both the resolve and
        # its retry unrecoverable (404, zero rows).
    conn = None
    try:
        conn = db.connect()  # inside try — a connect failure clears the claim, never sticks
        # Pass the session id as the durable resolution key: a retry (even after a
        # restart, on a fresh session object) returns the DB-persisted record.
        # The outcome is DERIVED from the earned ledger state; req.outcome is only a cross-check.
        record = runtime.resolve_session(session, conn, outcome=req.outcome, resolution_key=req.session_id)
    except ValueError as e:  # a caller-asserted outcome that contradicts the earned state
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
            # SCOPE, stated. These are WHOLE-DB figures across every conversation in the
            # database — both arms of a paired demo run, treated and untreated customers.
            # The dashboard's headline tiles are TREATED-SEGMENT, AFTER-arm figures, so the
            # two legitimately differ for the same DB; what was wrong was calling both
            # "save_rate" with no scope, leaving a reader to assume they should match (and
            # the dashboard's is the higher number).
            "scope": "all conversations in the database (all runs, all phases, all arms)",
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
