"""Conversation runtime — the agent loop (plan §3a).

OpenAI Responses API + tool calling, wrapped by the policy layer. Drives one
retention conversation from a scenario to a structured disposition.

Flow (plan §4 defense-in-depth; input/output guardrails arrive in Phase 2):
  1. disclosure.disclosure_message() is the first assistant turn (Art. 50).
  2. the model reasons and proposes tool calls; read tools execute directly,
     action tools are disposed by policy.authorize() before any effect.
  3. a simulated customer (sim.py) reacts; the loop resolves to saved / lost /
     escalated.
  4. the final disposition is produced via Structured Outputs and reconciled
     with the mechanically-known outcome (the loop is the source of truth).

Model IDs come from config. The conversation, its offer, and every policy
decision are persisted (conversations + audit_log).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import config
import db
import llm
import sim
from agent import disclosure, guardrails, policy, safety, tools

MAX_TURNS = 4          # agent<->customer exchanges before we call it lost
MAX_HOPS = 5           # tool-resolution hops within a single agent turn

DISPOSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "churn_reason": {"type": "string"},
        "offer_made": {"type": ["string", "null"]},
        "offer_accepted": {"type": "boolean"},
        "outcome": {"type": "string", "enum": ["saved", "lost", "escalated"]},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "churn_reason", "offer_made", "offer_accepted", "outcome", "confidence"],
    "additionalProperties": False,
}

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "description": "Short label for what the customer wanted, e.g. 'cancel_subscription'."},
        "churn_reason": {"type": "string", "description": "The customer's underlying reason for leaving."},
        "confidence": {"type": "number", "description": "0-1 confidence in this reading."},
    },
    "required": ["intent", "churn_reason", "confidence"],
    "additionalProperties": False,
}

SYSTEM = f"""You are a retention specialist for a SaaS company, talking to a customer who wants to cancel.

Operating principles:
- You are an AI. The customer has already been told this; do not re-disclose every turn.
- Bias to the next best action: understand why they're leaving, then make ONE concrete, policy-compliant retention offer that fits their reason.
- Calibrated autonomy: you may offer a discount or a pause yourself. Consequential/irreversible actions (downgrades, contract changes) require human approval — the tools will tell you.
- Fail safe and be honest: NEVER promise anything a tool did not authorize. If no offer is authorized (e.g. the customer is within a save-offer cooldown), do not invent one.
- When you have no authorized save offer and the customer simply wants to cancel, acknowledge warmly and let them go — process the cancellation gracefully. Do NOT reflexively escalate; only call escalate_to_human if the customer explicitly asks for a person or requests something consequential (a refund, a contract change) that you cannot handle.
- If the customer asks for something you have no tool for (e.g. upgrading to a higher plan) alongside the cancellation, DON'T hand off abruptly or abandon the chat. Acknowledge that specific request, tell them a teammate can set it up, and — if a retention offer is still on the table — keep helping with that in the same breath. Escalate only when the conversation genuinely cannot continue without a person.
- If you do escalate, never be terse: name what the customer asked for, say briefly why a teammate is better placed to help, and reassure them their conversation carries over.
- Keep replies short and human (2-4 sentences).

Tools: call get_customer / get_subscription / get_usage to ground yourself before offering. Then propose offer_pause or offer_discount. The policy layer may cap or reject your proposal; respect its verdict and only tell the customer what was actually authorized.

Retention playbook by reason:
- Price too high → a discount (within limits) or a pause.
- Missing integration / no longer needed → a pause is often the honest best offer; don't oversell.
- Switched to competitor → try once, but respect a firm no.
Discounts are capped at {config.MAX_DISCOUNT_PCT}%; pauses at {config.MAX_PAUSE_MONTHS} months."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_JAILBREAK_REPLY = ("I can't do that. I'm the retention assistant, so I can only help with your "
                    "subscription, billing, or cancellation — is there something there I can help with?")
_OFFSCOPE_REPLY = ("That's outside what I can help with — I'm just the retention assistant. "
                   "I can help with your subscription, billing, or cancellation, though.")


def _screen_input(text: str, rec: dict, *, classify_scope: bool = True, on_step=None) -> dict:
    """The SINGLE input-guardrail decision used by BOTH the batch and live paths.
    Returns {action, shown, reply?} where action ∈ {allow, bound, block}:
      block  — recognized jailbreak; the safe refusal is used and the input is
               NEVER forwarded to the model.
      bound  — off-scope; a bounded reply is used, the model is not free-formed.
      allow  — proceed to the agent.
    PII is redacted before anything is stored either way."""
    _emit(on_step, "input", "Screening the message (PII · injection · scope)…")
    s = guardrails.screen_input(text, classify_scope=classify_scope)
    if s["pii_types"]:
        rec["guardrail"].append(("pii", "redacted", ",".join(s["pii_types"])))
        _emit(on_step, "guardrail", f"PII redacted before storage: {', '.join(s['pii_types'])}", gtype="pii")
    shown = s["redacted_text"]
    if s["jailbreak"]["flagged"]:
        rec["guardrail"].append(("jailbreak", "blocked", s["jailbreak"]["reason"]))
        _emit(on_step, "guardrail", "Jailbreak / injection blocked — not forwarded to the model", gtype="jailbreak")
        return {"action": "block", "shown": shown, "reply": _JAILBREAK_REPLY}
    if s["off_scope"]:
        rec["guardrail"].append(("off_scope", "bounded", s["scope_reason"]))
        _emit(on_step, "guardrail", "Off-topic — bounded, not free-formed", gtype="off_scope")
        return {"action": "bound", "shown": shown, "reply": _OFFSCOPE_REPLY}
    return {"action": "allow", "shown": shown}


def _emit(on_step, kind: str, text: str, **extra) -> None:
    """Fire a step event for the live-turn legibility trace (no-op in batch)."""
    if on_step is not None:
        on_step({"kind": kind, "text": text, **extra})


def _compact(result) -> str:
    """One-line summary of a tool result for the trace."""
    if isinstance(result, dict):
        return " · ".join(str(v) for v in list(result.values())[:3])
    return str(result)[:60]


def _resolve_call(name: str, args: dict, cid: int, sub: dict, conn, rec: dict, on_step=None) -> dict:
    """Execute a read tool or dispose an action tool via policy. Records the
    authorized offer / escalation and writes audit_log rows."""
    if name in tools.READ_TOOLS:
        result = tools.read(conn, name, cid)
        rec["tool_results"].append(json.dumps(result))
        _emit(on_step, "tool", f"{name} → {_compact(result)}")
        return result

    verdict = policy.authorize(name, args, sub)
    rec["audit"].append(("policy", f"{name}:{verdict['action']}", verdict["reason"]))
    rec["policy_decisions"].append({"tool": name, **verdict})
    _emit(on_step, "policy", f"{name} → {verdict['action']}: {verdict['reason']}")

    if name == "escalate_to_human" and verdict["allowed"]:
        rec["escalated"] = True
        rec["escalate_reason"] = args.get("reason", "")
        return {"status": "escalated", "note": "Handed to a human agent."}

    if verdict["action"] in ("ok", "capped"):
        aargs = verdict["args"]
        if name == "offer_pause":
            rec["offer_made"] = f"{aargs['months']}-month pause"
        elif name == "offer_discount":
            rec["offer_made"] = f"{aargs['pct']:.0f}% discount"
        if verdict["action"] == "capped":  # action guardrail trip: proposed past a limit
            rec["guardrail"].append(("over_limit", "capped", verdict["reason"]))
        return {"status": "approved", "offer": rec["offer_made"], "note": verdict["reason"]}

    if verdict["action"] == "needs_human":  # consequential → human-in-the-loop (GDPR Art. 22)
        rec["guardrail"].append(("human_review", "routed", f"{name}: {verdict['reason']}"))
        rec["escalated"] = True  # a real state transition — the turn will hand off to a human
        rec["escalate_reason"] = f"{name} requires human approval"
        return {"status": "needs_human", "reason": verdict["reason"],
                "note": "This action needs a human — routing the customer to a teammate now."}

    # rejected — classify the block for observability
    gtype = "cooldown" if ("cooldown" in verdict["reason"].lower() or "save offer" in verdict["reason"].lower()) else "over_limit"
    rec["guardrail"].append((gtype, "rejected", verdict["reason"]))
    return {"status": "rejected", "reason": verdict["reason"]}


_HANDOFF_FALLBACK = ("Understood — I'm bringing in a teammate who can help with this. "
                     "They'll have our full conversation, so you won't need to repeat anything.")


def _handoff_message(input_list: list, system: str) -> str:
    """A warm, contextual closing message when the agent must escalate — the
    model writes it in its own words (no tools), with a safe fallback so a failed
    call never leaves the customer with a dead end."""
    try:
        resp = llm.client().responses.create(
            model=config.FLAGSHIP_MODEL,
            instructions=system + (
                "\n\nThis request needs a human teammate. Write ONLY a brief, warm closing message "
                "(1-2 sentences) to the customer: acknowledge their specific request by name, say a "
                "teammate will take it from here and already has the full conversation, and reassure "
                "them. Do not offer anything new; do not call tools."),
            input=input_list,
            reasoning={"effort": "low"},
            max_output_tokens=400,
        )
        return (resp.output_text or "").strip() or _HANDOFF_FALLBACK
    except Exception:
        return _HANDOFF_FALLBACK


_OUTPUT_SAFE_REPLY = ("I want to make sure I give you accurate, approved information — "
                      "let me bring in a teammate to help with this.")


def _finalize_output(text: str, rec: dict, input_list: list, system: str, on_step) -> str:
    """Enforce the output guardrails. If a check fires, regenerate once against a
    corrective instruction; if it still fails, FAIL CLOSED — substitute a safe
    reply and escalate. Nothing that trips promise/grounding/tone is delivered."""
    authorized_discount = any(d.get("tool") == "offer_discount" and d.get("allowed")
                              for d in rec["policy_decisions"])
    out = guardrails.screen_output(text, rec["tool_results"], authorized_discount=authorized_discount)
    flags = [k for k in ("promise", "grounding", "tone") if out[k]["flagged"]]
    if not flags:
        _emit(on_step, "output", "Output guardrails passed")
        return text
    # one corrective regeneration
    fixed = None
    try:
        resp = llm.client().responses.create(
            model=config.FLAGSHIP_MODEL,
            instructions=system + ("\n\nYour previous reply was blocked by a safety check ("
                + ", ".join(flags) + "). Rewrite it: promise ONLY what a tool authorized, state no "
                "account facts a tool did not return, keep a professional tone. Do not call tools."),
            input=input_list, reasoning={"effort": "low"}, max_output_tokens=600)
        cand = (resp.output_text or "").strip()
        if cand:
            out2 = guardrails.screen_output(cand, rec["tool_results"], authorized_discount=authorized_discount)
            if not any(out2[k]["flagged"] for k in ("promise", "grounding", "tone")):
                fixed = cand
    except Exception:
        fixed = None
    if fixed:
        for k in flags:
            rec["guardrail"].append((k, "regenerated", out[k]["reason"]))
        _emit(on_step, "output", f"Output guardrail caught {', '.join(flags)} — safely regenerated")
        return fixed
    for k in flags:
        rec["guardrail"].append((k, "blocked", out[k]["reason"]))
    rec["escalated"] = True
    _emit(on_step, "output", f"Output guardrail blocked {', '.join(flags)} — escalating")
    return _OUTPUT_SAFE_REPLY


def _agent_turn(input_list: list, cid: int, sub: dict, conn, rec: dict, system: str = SYSTEM, on_step=None) -> str:
    """Produce one agent reply, resolving any tool calls first."""
    for hop in range(MAX_HOPS):
        _emit(on_step, "think", "Reasoning about the best next action…" if hop == 0 else "Working…")
        resp = llm.client().responses.create(
            model=config.FLAGSHIP_MODEL,
            instructions=system,
            input=input_list,
            tools=tools.TOOL_SCHEMAS,
            tool_choice="auto",
            reasoning={"effort": "low"},
            max_output_tokens=1400,
        )
        calls = [it for it in resp.output if getattr(it, "type", None) == "function_call"]
        if not calls:
            text = resp.output_text or "Thanks — is there anything else I can help with?"
            text = _finalize_output(text, rec, input_list, system, on_step)
            input_list.append({"role": "assistant", "content": text})
            return text
        for call in calls:
            input_list.append({"type": "function_call", "call_id": call.call_id,
                               "name": call.name, "arguments": call.arguments})
            args = json.loads(call.arguments or "{}")
            result = _resolve_call(call.name, args, cid, sub, conn, rec, on_step=on_step)
            input_list.append({"type": "function_call_output", "call_id": call.call_id,
                               "output": json.dumps(result)})
        # An escalation resolves the turn — don't keep spinning tool hops.
        if rec["escalated"]:
            _emit(on_step, "output", "Preparing a hand-off to a human teammate")
            msg = _handoff_message(input_list, system)
            input_list.append({"role": "assistant", "content": msg})
            return msg
    return "Let me connect you with a teammate to make sure this is handled properly."


def _advance(text: str, transcript: list, input_list: list, cid: int, sub: dict, conn, rec: dict,
             *, system: str = SYSTEM, on_step=None) -> str:
    """One agent response to one user message — the SHARED core of both the batch
    and live paths. Screens the input (block / bound / allow), then either
    short-circuits with a safe reply (model not called) or runs the agent turn.
    A blocked jailbreak is shown in the transcript but never enters the model
    input. Returns the assistant reply."""
    dec = _screen_input(text, rec, classify_scope=True, on_step=on_step)
    transcript.append({"role": "user", "content": dec["shown"]})
    if dec["action"] == "block":
        reply = dec["reply"]  # NOT added to input_list — the injection never reaches the model
    elif dec["action"] == "bound":
        input_list.append({"role": "user", "content": dec["shown"]})
        input_list.append({"role": "assistant", "content": dec["reply"]})
        reply = dec["reply"]
    else:
        input_list.append({"role": "user", "content": dec["shown"]})
        reply = _agent_turn(input_list, cid, sub, conn, rec, system=system, on_step=on_step)
    transcript.append({"role": "assistant", "content": reply})
    _emit(on_step, "reply", reply)
    return reply


def _disposition(transcript: list, scenario: dict, rec: dict, outcome: str, offer_accepted: bool) -> dict:
    """Structured-output reading of intent/reason/confidence, reconciled with
    the mechanically-known outcome/offer (the loop is the source of truth)."""
    convo = "\n".join(f"{t['role']}: {t['content']}" for t in transcript)
    try:
        read = llm.structured(
            model=config.MINI_MODEL,
            instructions="Read this retention conversation and extract the customer's intent, churn reason, and your confidence.",
            user_input=convo,
            schema=_INTENT_SCHEMA,
            name="disposition_read",
            reasoning_effort="minimal",
            max_output_tokens=500,
        )
    except Exception:
        read = {"intent": "cancel_subscription",
                "churn_reason": scenario.get("churn_reason", "unknown"), "confidence": 0.5}
    confidence = round(float(read["confidence"]), 2)
    if confidence < config.JUDGE_CONFIDENCE_FLOOR:
        rec["guardrail"].append(("low_confidence", "flagged",
                                 f"disposition confidence {confidence} < floor {config.JUDGE_CONFIDENCE_FLOOR}"))
    return {
        "intent": read["intent"],
        "churn_reason": read["churn_reason"],
        "offer_made": rec["offer_made"],
        "offer_accepted": offer_accepted,
        "outcome": outcome,
        "confidence": confidence,
    }


def simulate_conversation(scenario: dict, conn, *, system: str = SYSTEM) -> dict:
    """Run one conversation to completion WITHOUT writing to the DB (reads only).

    Returns a record ready for persist_conversation. Because it never writes, it
    is safe to run many of these concurrently, each with its own read connection;
    persistence is centralized on the main thread (see batch.py). `system` lets a
    caller inject a modified agent prompt (used to demo the eval catching a
    broken agent, and to run before/after policy batches)."""
    cid = scenario["customer_id"]
    sub = tools.get_subscription(conn, cid)
    rec = {"offer_made": None, "escalated": False, "policy_decisions": [],
           "tool_results": [], "guardrail": [],
           "audit": [("system", "ai_disclosure_shown", "EU AI Act Art. 50")]}

    transcript = [disclosure.disclosure_message()]
    input_list = list(transcript)

    outcome, offer_accepted = None, False
    pending = scenario["opening_message"]  # the current user message to process
    for _ in range(MAX_TURNS):
        reply = _advance(pending, transcript, input_list, cid, sub, conn, rec, system=system)
        if rec["escalated"]:
            outcome = "escalated"
            break
        cust = sim.respond(scenario, reply, transcript)
        if cust["decision"] == "accept":
            # A save requires an accepted *retention offer*. "Yes, cancel me" with
            # no offer on the table is the customer accepting cancellation = churn.
            outcome, offer_accepted = ("saved", True) if rec["offer_made"] else ("lost", False)
            break
        if cust["decision"] == "reject":
            outcome = "lost"
            break
        pending = cust["reply"]  # screened by _advance on the next iteration
    if outcome is None:
        outcome = "lost"

    disp = _disposition(transcript, scenario, rec, outcome, offer_accepted)
    return {"customer_id": cid, "scenario_id": scenario["id"], "transcript": transcript,
            "disposition": disp, "outcome": outcome, "offer_made": rec["offer_made"],
            "policy_decisions": rec["policy_decisions"], "guardrail_events": rec["guardrail"],
            "audit": rec["audit"]}


def persist_conversation(conn, record: dict) -> int:
    """Write a simulated conversation + its audit/guardrail rows. Returns the
    new conversation id and stamps it onto the record. Call on the main thread."""
    # De-identify before storage/embedding: user turns are already redacted at
    # input, but redact assistant turns too — so any account PII the model echoes
    # back is scrubbed before it's logged or clustered (data minimization).
    stored_transcript = [
        {"role": t["role"],
         "content": guardrails.redact_pii(t["content"])[0] if t.get("role") == "assistant" else t["content"]}
        for t in record["transcript"]
    ]
    cur = conn.execute(
        "INSERT INTO conversations "
        "(customer_id, scenario_id, transcript_json, disposition_json, offer_made, outcome, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (record["customer_id"], record["scenario_id"], db.dumps(stored_transcript),
         db.dumps(record["disposition"]), record["offer_made"], record["outcome"], _now()),
    )
    conv_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO audit_log (conversation_id, actor, decision, reason, created_at) VALUES (?,?,?,?,?)",
        [(conv_id, a, d, r, _now()) for (a, d, r) in record["audit"]],
    )
    conn.executemany(
        "INSERT INTO guardrail_events (conversation_id, type, action, detail, created_at) VALUES (?,?,?,?,?)",
        [(conv_id, t, a, d, _now()) for (t, a, d) in record["guardrail_events"]],
    )
    # Persist cooldown state: extending a save offer starts the 90-day clock, so a
    # later conversation for the same customer won't immediately offer again.
    if record.get("offer_made"):
        conn.execute("UPDATE subscriptions SET last_save_offer_days = 0 WHERE customer_id = ?",
                     (record["customer_id"],))
    conn.commit()
    record["conversation_id"] = conv_id
    return conv_id


def run_conversation(scenario: dict, conn, *, system: str = SYSTEM) -> dict:
    """Simulate one conversation and persist it (single-conversation convenience)."""
    record = simulate_conversation(scenario, conn, system=system)
    persist_conversation(conn, record)
    return record


# ---------------------------------------------------------------------------
# Live conversation API — the HUMAN is the customer (the console / web widget).
# Reuses the exact screening / agent / policy / output logic above; the only
# difference from the batch path is that turns are driven by real user input
# instead of the simulated customer, and each turn emits a step trace so the
# latency is filled with legibility (what the agent is doing right now).
# ---------------------------------------------------------------------------
def new_session(customer_id: int, conn) -> dict:
    """Start a live conversation with the AI-disclosure turn already shown."""
    sub = tools.get_subscription(conn, customer_id)
    customer = tools.get_customer(conn, customer_id)
    rec = {"offer_made": None, "escalated": False, "policy_decisions": [],
           "tool_results": [], "guardrail": [],
           "audit": [("system", "ai_disclosure_shown", "EU AI Act Art. 50")]}
    disc = disclosure.disclosure_message()
    state = safety.program_state(conn)  # kill switch — start in safe mode if unhealthy
    return {"customer_id": customer_id, "sub": sub, "customer": customer, "rec": rec,
            "transcript": [disc], "input_list": [disc], "outcome": None,
            "disclosure": disc["content"], "safe_mode": not state["healthy"],
            "safety_reasons": state["reasons"]}


def _turn_result(session: dict, reply: str, new_events: list) -> dict:
    rec = session["rec"]
    return {"reply": reply, "escalated": rec["escalated"], "offer_made": rec["offer_made"],
            "outcome": session["outcome"], "new_guardrail_events": new_events,
            "guardrail_events": list(rec["guardrail"])}


def live_turn(session: dict, user_text: str, conn, *, on_step=None) -> dict:
    """Advance one live turn using the SAME shared core (_advance) as the batch
    runner: a jailbreak is blocked before the model, off-scope is bounded,
    otherwise the agent runs; a needs-human or output violation escalates."""
    rec = session["rec"]
    # Kill switch: if the program is in safe mode, disclose + route to a human
    # instead of running the agent autonomously.
    if session.get("safe_mode"):
        _emit(on_step, "guardrail", "Safe mode active — routing to a human teammate", gtype="safe_mode")
        session["transcript"].append({"role": "user", "content": guardrails.redact_pii(user_text)[0]})
        reply = ("Thanks for reaching out. To make sure you get the best help right now, I'm "
                 "connecting you with a human teammate who can take it from here.")
        session["transcript"].append({"role": "assistant", "content": reply})
        ev = ("safe_mode", "engaged", "; ".join(session.get("safety_reasons", [])) or "kill switch")
        rec["guardrail"].append(ev)
        rec["escalated"] = True
        session["outcome"] = "escalated"
        _emit(on_step, "reply", reply)
        return _turn_result(session, reply, [ev])
    before = len(rec["guardrail"])
    reply = _advance(user_text, session["transcript"], session["input_list"],
                     session["customer_id"], session["sub"], conn, rec, on_step=on_step)
    if rec["escalated"]:
        session["outcome"] = "escalated"
    return _turn_result(session, reply, rec["guardrail"][before:])


def resolve_session(session: dict, outcome: str, conn) -> dict:
    """End a live conversation: VALIDATE the outcome, persist it, and GRADE it
    (so the live path also grades 100%). Raises ValueError on an invalid
    transition (already resolved, or 'saved' without an accepted offer)."""
    rec = session["rec"]
    if session.get("resolved"):
        raise ValueError("this conversation has already been resolved")
    if session["outcome"] == "escalated":
        outcome = "escalated"
    if outcome not in ("saved", "lost", "escalated"):
        raise ValueError("outcome must be saved, lost, or escalated")
    if outcome == "saved" and not rec["offer_made"]:
        raise ValueError("cannot mark 'saved' without an authorized, accepted retention offer")
    offer_accepted = outcome == "saved"
    scenario = {"id": None, "customer_id": session["customer_id"], "churn_reason": "live session"}
    disp = _disposition(session["transcript"], scenario, rec, outcome, offer_accepted)
    record = {"customer_id": session["customer_id"], "scenario_id": None,
              "transcript": session["transcript"], "disposition": disp, "outcome": outcome,
              "offer_made": rec["offer_made"], "policy_decisions": rec["policy_decisions"],
              "guardrail_events": rec["guardrail"], "audit": rec["audit"]}
    persist_conversation(conn, record)
    _grade_and_store(conn, record["conversation_id"], record, session["customer_id"])
    session["resolved"] = True
    return record


def _grade_and_store(conn, conv_id: int, record: dict, customer_id: int) -> None:
    """Grade a persisted conversation and write its eval row, so the live path
    grades 100% of conversations. A judge failure writes a coverage-miss eval
    (verdict 'error') rather than silently leaving the record ungraded."""
    from evals import judge  # local import avoids any load-order cycle
    row = conn.execute("SELECT demographic_attr FROM customers WHERE id=?", (customer_id,)).fetchone()
    convo = {"transcript": record["transcript"], "disposition": record["disposition"],
             "demographic_attr": row["demographic_attr"] if row else "unknown",
             "policy_decisions": record.get("policy_decisions", []),
             "guardrail_events": record.get("guardrail_events", [])}
    try:
        v = judge.judge_conversation(convo)
        verdict = judge.derive_verdict(v["scores"])
        conn.execute(
            "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (conv_id, db.dumps(v["scores"]), verdict, v["rationale"], int(v["fairness_flag"]), _now()))
    except Exception as e:
        conn.execute(
            "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (conv_id, db.dumps({}), "error", f"grading failed: {type(e).__name__}", 0, _now()))
    conn.commit()
