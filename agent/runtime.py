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
from agent import disclosure, guardrails, policy, tools

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
- Keep replies short and human (2-4 sentences).

Tools: call get_customer / get_subscription / get_usage to ground yourself before offering. Then propose offer_pause or offer_discount. The policy layer may cap or reject your proposal; respect its verdict and only tell the customer what was actually authorized.

Retention playbook by reason:
- Price too high → a discount (within limits) or a pause.
- Missing integration / no longer needed → a pause is often the honest best offer; don't oversell.
- Switched to competitor → try once, but respect a firm no.
Discounts are capped at {config.MAX_DISCOUNT_PCT}%; pauses at {config.MAX_PAUSE_MONTHS} months."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _screen_and_record(text: str, rec: dict, *, classify_scope: bool) -> str:
    """Run input guardrails on a user turn, record any trips, return the
    redacted text that is safe to store and send to the model."""
    s = guardrails.screen_input(text, classify_scope=classify_scope)
    if s["pii_types"]:
        rec["guardrail"].append(("pii", "redacted", ",".join(s["pii_types"])))
    if s["jailbreak"]["flagged"]:
        rec["guardrail"].append(("jailbreak", "blocked", s["jailbreak"]["reason"]))
    if s["off_scope"]:
        rec["guardrail"].append(("off_scope", "bounded", s["scope_reason"]))
    return s["redacted_text"]


def _resolve_call(name: str, args: dict, cid: int, sub: dict, conn, rec: dict) -> dict:
    """Execute a read tool or dispose an action tool via policy. Records the
    authorized offer / escalation and writes audit_log rows."""
    if name in tools.READ_TOOLS:
        result = tools.read(conn, name, cid)
        rec["tool_results"].append(json.dumps(result))
        return result

    verdict = policy.authorize(name, args, sub)
    rec["audit"].append(("policy", f"{name}:{verdict['action']}", verdict["reason"]))
    rec["policy_decisions"].append({"tool": name, **verdict})

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
        return {"status": "needs_human", "reason": verdict["reason"]}

    # rejected — classify the block for observability
    gtype = "cooldown" if ("cooldown" in verdict["reason"].lower() or "save offer" in verdict["reason"].lower()) else "over_limit"
    rec["guardrail"].append((gtype, "rejected", verdict["reason"]))
    return {"status": "rejected", "reason": verdict["reason"]}


def _agent_turn(input_list: list, cid: int, sub: dict, conn, rec: dict) -> str:
    """Produce one agent reply, resolving any tool calls first."""
    for _ in range(MAX_HOPS):
        resp = llm.client().responses.create(
            model=config.FLAGSHIP_MODEL,
            instructions=SYSTEM,
            input=input_list,
            tools=tools.TOOL_SCHEMAS,
            tool_choice="auto",
            reasoning={"effort": "low"},
            max_output_tokens=1400,
        )
        calls = [it for it in resp.output if getattr(it, "type", None) == "function_call"]
        if not calls:
            text = resp.output_text or "Thanks — is there anything else I can help with?"
            out = guardrails.screen_output(text, rec["tool_results"])
            for kind in ("promise", "grounding"):
                if out[kind]["flagged"]:
                    rec["guardrail"].append((f"{kind}", "flagged", out[kind]["reason"]))
            if out["tone"]["flagged"]:
                rec["guardrail"].append(("tone", "blocked", out["tone"]["reason"]))
                rec["escalated"] = True
                text = "I want to make sure this is handled well — let me bring in a teammate."
            input_list.append({"role": "assistant", "content": text})
            return text
        for call in calls:
            input_list.append({"type": "function_call", "call_id": call.call_id,
                               "name": call.name, "arguments": call.arguments})
            args = json.loads(call.arguments or "{}")
            result = _resolve_call(call.name, args, cid, sub, conn, rec)
            input_list.append({"type": "function_call_output", "call_id": call.call_id,
                               "output": json.dumps(result)})
        # An escalation resolves the turn — don't keep spinning tool hops.
        if rec["escalated"]:
            msg = "Understood — I'm connecting you with a teammate who can take it from here."
            input_list.append({"role": "assistant", "content": msg})
            return msg
    return "Let me connect you with a teammate to make sure this is handled properly."


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
    return {
        "intent": read["intent"],
        "churn_reason": read["churn_reason"],
        "offer_made": rec["offer_made"],
        "offer_accepted": offer_accepted,
        "outcome": outcome,
        "confidence": round(float(read["confidence"]), 2),
    }


def run_conversation(scenario: dict, conn, *, model: str = config.FLAGSHIP_MODEL) -> dict:
    """Run one conversation to completion; persist it; return the stored record."""
    cid = scenario["customer_id"]
    sub = tools.get_subscription(conn, cid)
    rec = {"offer_made": None, "escalated": False, "policy_decisions": [],
           "tool_results": [], "guardrail": [],
           "audit": [("system", "ai_disclosure_shown", "EU AI Act Art. 50")]}

    # Input guardrails on the opening turn: redact PII BEFORE it is stored,
    # screen for jailbreak/off-scope (scope classified at entry only).
    opening = _screen_and_record(scenario["opening_message"], rec, classify_scope=True)
    transcript = [disclosure.disclosure_message(), {"role": "user", "content": opening}]
    input_list = list(transcript)

    outcome, offer_accepted = None, False
    for _ in range(MAX_TURNS):
        reply = _agent_turn(input_list, cid, sub, conn, rec)
        transcript.append({"role": "assistant", "content": reply})
        if rec["escalated"]:
            outcome = "escalated"
            break
        cust = sim.respond(scenario, reply, transcript)
        cust_text = _screen_and_record(cust["reply"], rec, classify_scope=False)
        transcript.append({"role": "user", "content": cust_text})
        input_list.append({"role": "user", "content": cust_text})
        if cust["decision"] == "accept":
            # A save requires an accepted *retention offer*. "Yes, cancel me" with
            # no offer on the table is the customer accepting cancellation = churn.
            if rec["offer_made"]:
                outcome, offer_accepted = "saved", True
            else:
                outcome = "lost"
            break
        if cust["decision"] == "reject":
            outcome = "lost"
            break
    if outcome is None:
        outcome = "lost"

    disp = _disposition(transcript, scenario, rec, outcome, offer_accepted)

    cur = conn.execute(
        "INSERT INTO conversations "
        "(customer_id, scenario_id, transcript_json, disposition_json, offer_made, outcome, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, scenario["id"], db.dumps(transcript), db.dumps(disp), rec["offer_made"], outcome, _now()),
    )
    conv_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO audit_log (conversation_id, actor, decision, reason, created_at) VALUES (?,?,?,?,?)",
        [(conv_id, actor, decision, reason, _now()) for (actor, decision, reason) in rec["audit"]],
    )
    conn.executemany(
        "INSERT INTO guardrail_events (conversation_id, type, action, detail, created_at) VALUES (?,?,?,?,?)",
        [(conv_id, gtype, action, detail, _now()) for (gtype, action, detail) in rec["guardrail"]],
    )
    conn.commit()
    return {"conversation_id": conv_id, "transcript": transcript, "disposition": disp,
            "outcome": outcome, "offer_made": rec["offer_made"],
            "policy_decisions": rec["policy_decisions"], "guardrail_events": rec["guardrail"]}
