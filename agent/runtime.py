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
import re
from datetime import datetime, timezone

import config
import db
import llm
import sim
from agent import disclosure, guardrails, offers, policy, safety, tools

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
- Hold firm at the ceiling: the policy layer authorizes a MAXIMUM (e.g. up to 20% off, up to a 3-month pause). Present at most that. If the customer pushes for more, warmly hold firm — offer the maximum you're authorized for and let them decide; do NOT exceed it, and do NOT hand off to a human just because they want a bigger discount. Wanting more is not grounds for escalation.
- Offer, don't confirm: you PROPOSE terms — you do not operate the billing backend. Say "I can apply 20% off" or "I'd set up a 3-month pause", never "I've applied the discount" or "your pause is set up". Present the authorized offer as something you're extending, not something already done.
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


def _new_rec() -> dict:
    """Fresh per-conversation state. The `offers` ledger is the single source of
    truth for what was authorized, presented, and accepted (see agent/offers.py);
    everything downstream — outcome, cooldown, economics, the eval envelope —
    derives from it rather than from a scattered last-write string. `tool_facts`
    holds read-tool results (with call ids) so grounding can be checked per claim,
    and `policy_decisions` keeps the ADJUSTED args so the persisted eval envelope
    is lossless."""
    return {"offers": [], "escalated": False, "policy_decisions": [],
            "tool_facts": [], "guardrail": [],
            "audit": [("system", "ai_disclosure_shown", "EU AI Act Art. 50")]}


def _tool_result_strings(rec: dict) -> list[str]:
    """Read-tool results as JSON strings — the corpus the grounding check scans."""
    return [json.dumps(f["result"]) for f in rec["tool_facts"]]


def _offer_made(rec: dict) -> str | None:
    """The canonical single-offer string, derived from the ledger (accepted first,
    else presented). Replaces the old last-write `offer_made`."""
    return offers.offer_summary(rec["offers"])


def _evidence(rec: dict) -> dict:
    """The canonical eval envelope — the LOSSLESS execution record persisted with a
    conversation so the batch judge and the live judge grade from IDENTICAL
    evidence: the full offer ledger (exact authorized + presented terms), the
    read-tool facts (for grounding), and the policy decisions WITH their adjusted
    args. Replaces the old lossy `tool:action` audit-string reconstruction."""
    return {"offers": [o.to_dict() for o in rec["offers"]],
            "tool_facts": rec["tool_facts"],
            "policy_decisions": rec["policy_decisions"]}


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
        # `shown` goes in the human-visible transcript for fidelity, but the model
        # context gets only a neutral marker — off-scope (possibly adversarial)
        # content must not steer any later in-scope turn.
        return {"action": "bound", "shown": shown, "reply": _OFFSCOPE_REPLY,
                "context": "[off-topic message from the customer; bounded and withheld from context]"}
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


def _resolve_call(name: str, args: dict, cid: int, sub: dict, conn, rec: dict, *,
                  call_id: str | None = None, on_step=None) -> dict:
    """Execute a read tool or dispose an action tool via policy. A read tool's
    result is recorded as a grounded fact (with its call id); an authorized offer
    becomes a ledger entry. Writes audit_log rows."""
    if name in tools.READ_TOOLS:
        result = tools.read(conn, name, cid)
        rec["tool_facts"].append({"tool": name, "call_id": call_id, "result": result})
        _emit(on_step, "tool", f"{name} → {_compact(result)}")
        return result

    verdict = policy.authorize(name, args, sub)
    rec["audit"].append(("policy", f"{name}:{verdict['action']}", verdict["reason"]))
    # Keep the ADJUSTED args on the persisted decision so the eval envelope is
    # lossless (the batch judge must see 10% vs 20%, not just tool:action).
    rec["policy_decisions"].append({"tool": name, "action": verdict["action"],
                                    "args": verdict.get("args"), "reason": verdict["reason"]})
    _emit(on_step, "policy", f"{name} → {verdict['action']}: {verdict['reason']}")

    if name == "escalate_to_human" and verdict["allowed"]:
        rec["escalated"] = True
        rec["escalate_reason"] = args.get("reason", "")
        return {"status": "escalated", "note": "Handed to a human agent."}

    if verdict["action"] in ("ok", "capped"):
        aargs = verdict["args"]
        kind = "pause" if name == "offer_pause" else "discount"
        # Authorize into the ledger — supersedes any prior live offer, so exactly
        # one offer is ever active (the "one concrete offer" rule, enforced).
        off = offers.authorize(rec["offers"], kind, aargs)
        if verdict["action"] == "capped":  # action guardrail trip: proposed past a limit
            rec["guardrail"].append(("over_limit", "capped", verdict["reason"]))
        # The tool tells the model the CEILING it may present, and that it must
        # not claim the action is already done — the offer is authorized, not applied.
        return {"status": "authorized", "offer_kind": kind,
                "authorized_terms": offers.human_terms(off, aargs),
                "note": verdict["reason"] + " You may present up to these terms; do not exceed them, "
                        "and offer them (do not claim they are already applied)."}

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


def _handoff_safe(text: str, rec: dict) -> bool:
    """A hand-off must make NO offer, state NO dollar amount, and claim NO completed
    action — it just routes to a human. Same fact-free bar as the empathy prose, so
    the escalation path is not a weaker output channel than an ordinary reply."""
    if not text:
        return False
    if guardrails.extract_discount_pcts(text) or guardrails.extract_pause_months(text):
        return False  # a hand-off must not float an offer
    if _MONEY_IN_TEXT.search(text):
        return False  # …nor state a dollar figure
    return not guardrails.check_completion_claim(text)


def _handoff_message(input_list: list, system: str, rec: dict | None = None) -> str:
    """A warm, contextual closing message when the agent must escalate. The model
    writes it, but it is SCREENED with the same deterministic output checks as a
    normal reply (no offer, no invented account fact); anything that fails those
    checks falls back to a fixed safe message — the hand-off path is not a weaker
    output channel than an ordinary turn."""
    rec = rec if rec is not None else _new_rec()
    try:
        resp = llm.client().responses.create(
            model=config.FLAGSHIP_MODEL,
            instructions=system + (
                "\n\nThis request needs a human teammate. Write ONLY a brief, warm closing message "
                "(1-2 sentences) to the customer: acknowledge their specific request, say a teammate "
                "will take it from here and already has the full conversation, and reassure them. "
                "Make NO offer and state NO specific account numbers/prices; do not call tools."),
            input=input_list,
            reasoning={"effort": "low"},
            max_output_tokens=400,
        )
        text = (resp.output_text or "").strip()
        return text if _handoff_safe(text, rec) else _HANDOFF_FALLBACK
    except Exception:
        return _HANDOFF_FALLBACK


_OUTPUT_SAFE_REPLY = ("I want to make sure I give you accurate, approved information — "
                      "let me bring in a teammate to help with this.")

# SERVER-AUTHORITATIVE OUTPUT. The model never writes the customer-facing FACTS.
# It returns only non-factual empathy prose plus STRUCTURED references (an offer by
# kind+terms, and account facts by field+source-tool). The server validates those
# against the offer ledger and the tool results, then RENDERS every factual sentence
# itself from a fixed template. This removes the whole class of "the prose expressed
# a bigger number than it declared" bugs: the customer only ever reads facts the
# server generated from validated data, and the empathy prose is checked to contain
# no offer terms or dollar amounts at all.
_CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "empathy_text": {
            "type": "string",
            "description": ("Warm, human framing (1-3 sentences). MUST NOT contain any discount percentage, "
                            "pause length, dollar amount, or account number — the system renders every fact. "
                            "Acknowledge the customer and set up the offer or the cancellation in feeling, "
                            "not in numbers."),
        },
        "offer": {
            "type": "object",
            "description": "The single retention offer to present, or kind='none'.",
            "properties": {
                "kind": {"type": "string", "enum": ["discount", "pause", "none"]},
                "pct": {"type": ["number", "null"], "description": "Discount percent (kind=discount), else null."},
                "months": {"type": ["integer", "null"], "description": "Pause months (kind=pause), else null."},
            },
            "required": ["kind", "pct", "months"],
            "additionalProperties": False,
        },
        "account_facts": {
            "type": "array",
            "description": "Account facts to state, BY REFERENCE — the server renders the value.",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "The field name, e.g. 'price' or 'plan'."},
                    "source_tool": {"type": "string", "description": "The tool that returned it."},
                },
                "required": ["field", "source_tool"],
                "additionalProperties": False,
            },
        },
        "process_cancellation": {
            "type": "boolean",
            "description": "True if the customer should be told their cancellation will be processed.",
        },
    },
    "required": ["empathy_text", "offer", "account_facts", "process_cancellation"],
    "additionalProperties": False,
}

_CONTRACT_INSTRUCTIONS = (
    "\n\nProduce your reply as a SERVER-RENDERED contract. You write ONLY the feeling; the system writes "
    "every fact and every action sentence — do not restate them yourself:\n"
    "- empathy_text: 1-2 warm, human sentences of acknowledgement ONLY. Do NOT state the offer, do NOT say "
    "you'll process/apply/cancel anything, and do NOT mention any percentage, dollar amount, pause length, "
    "plan, or account number — the system appends the exact offer and action sentences after your words. "
    "Just make the customer feel heard.\n"
    "- offer: the ONE retention offer to present, as kind + exact terms, or kind='none'. Terms must not "
    "exceed what the tool results authorized (if a tool authorized 'up to 20%', use at most 20 — never more, "
    "even if the customer demands it). Never reference an offer no tool authorized.\n"
    "- account_facts: leave EMPTY unless the customer explicitly asked about a specific account fact; then "
    "reference it by field + the tool that returned it (the system fills in the value). Never volunteer "
    "account facts.\n"
    "- process_cancellation: true when you are letting the customer go.\n"
    "Do not call tools."
)


def _generate_contract(input_list: list, system: str, *, corrective: str = "") -> dict:
    """Ask the model for the structured response contract over the full turn
    context. Raises on failure (handled by the caller as fail-closed)."""
    return llm.structured(
        config.FLAGSHIP_MODEL, system + _CONTRACT_INSTRUCTIONS + (
            f"\n\nYour previous draft was REJECTED: {corrective}. Fix it — present at most the authorized "
            f"maximum (never exceed a ceiling), state only tool-provided account facts, and do not claim "
            f"an offer is already applied. If the customer wanted more than you can give, hold firm at the "
            f"maximum rather than exceeding it." if corrective else ""),
        input_list, _CONTRACT_SCHEMA, "response_contract",
        reasoning_effort="low", max_output_tokens=900)


_MONEY_IN_TEXT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def _validate_contract(contract: dict, rec: dict) -> tuple[bool, str]:
    """Validate the SERVER-AUTHORITATIVE contract. The model's empathy prose must be
    FACT-FREE (the server renders every fact); the offer must be an authorized ledger
    offer within its ceiling; each account_fact must reference a field a cited tool
    actually returned. Returns (ok, reason)."""
    empathy = contract.get("empathy_text") or ""
    # 1) empathy prose carries NO facts — no offer terms, no dollar amounts — so the
    #    model cannot smuggle a bigger number than the structured offer records.
    if guardrails.extract_discount_pcts(empathy) or guardrails.extract_pause_months(empathy):
        return False, "empathy_text contains offer terms; keep prose fact-free (the system renders the offer)"
    if _MONEY_IN_TEXT.search(empathy):
        return False, "empathy_text contains a dollar amount; the system renders account facts"
    if guardrails.check_completion_claim(empathy):
        return False, "empathy_text claims an action is already applied (tools only propose)"

    # 2) the offer must be an authorized ledger offer of its kind, within the ceiling
    offer = contract.get("offer") or {"kind": "none"}
    kind = offer.get("kind")
    if kind in ("discount", "pause"):
        terms = {"pct": offer.get("pct")} if kind == "discount" else {"months": offer.get("months")}
        if (kind == "discount" and offer.get("pct") is None) or (kind == "pause" and offer.get("months") is None):
            return False, "offer is missing its terms"
        match = offers.offer_of_kind(rec["offers"], kind)
        if match is None:
            return False, f"offer presents a {kind} that policy did not authorize"
        if not offers.terms_within(terms, match.authorized_terms, kind):
            return False, f"offer {terms} exceeds the authorized ceiling {match.authorized_terms}"

    # 3) each account_fact must reference a field a cited tool actually returned
    for f in contract.get("account_facts", []):
        if _fact_value(rec, f.get("field"), f.get("source_tool")) is None:
            return False, (f"account_fact '{f.get('field')}' from '{f.get('source_tool')}' "
                           f"was not returned by that tool")
    return True, ""


def _fact_value(rec: dict, field: str, tool: str):
    """The value of an account field from a specific tool's result, or None if that
    tool didn't run or didn't return the field."""
    for fct in rec["tool_facts"]:
        if fct.get("tool") == tool and isinstance(fct.get("result"), dict) and field in fct["result"]:
            return fct["result"][field]
    return None


def _offer_sentence(kind: str, terms: dict) -> str:
    """The SERVER's rendering of the offer — the ONLY place an offer's exact terms
    become customer-facing text."""
    if kind == "discount":
        return f"I can apply {float(terms['pct']):.0f}% off your subscription going forward."
    if kind == "pause":
        return f"I can set up a {int(terms['months'])}-month pause on your plan, so you won't be billed during it."
    return ""


_DOLLAR_FIELDS = ("price", "amount", "balance", "credit", "total", "fee")


def _fact_sentence(field: str, value) -> str:
    label = field.replace("_", " ")
    if field in _DOLLAR_FIELDS and isinstance(value, (int, float)):
        return f"Your {label} is ${float(value):.2f}."
    return f"Your {label} is {value}."


def _render_reply(contract: dict, rec: dict) -> str:
    """Assemble the customer-facing reply: the model's fact-free empathy prose, then
    every FACTUAL sentence rendered by the SERVER from validated data (account facts,
    the offer, the cancellation) — the customer never reads a model-authored fact."""
    parts = [(contract.get("empathy_text") or "").strip()]
    for f in contract.get("account_facts", []):
        val = _fact_value(rec, f.get("field"), f.get("source_tool"))
        if val is not None:
            parts.append(_fact_sentence(f["field"], val))
    offer = contract.get("offer") or {"kind": "none"}
    if offer.get("kind") in ("discount", "pause"):
        terms = {"pct": offer.get("pct")} if offer["kind"] == "discount" else {"months": offer.get("months")}
        parts.append(_offer_sentence(offer["kind"], terms))
        parts.append("Would you like to go ahead with that, or should I process the cancellation?")
    elif contract.get("process_cancellation"):
        parts.append("I'll process your cancellation, and you'll keep access through the end of your "
                     "current billing period.")
    return " ".join(p for p in parts if p)


def _apply_contract(contract: dict, rec: dict) -> None:
    """Mark the offered ledger entry presented with the exact offered terms (so
    outcome/economics use presented, not authorized, terms)."""
    offer = contract.get("offer") or {"kind": "none"}
    kind = offer.get("kind")
    if kind not in ("discount", "pause"):
        return
    terms = {"pct": float(offer["pct"])} if kind == "discount" else {"months": int(offer["months"])}
    match = offers.offer_of_kind(rec["offers"], kind)
    if match is not None:
        offers.present(rec["offers"], match, terms)


def _screen_contract(input_list: list, system: str, rec: dict, corrective: str) -> tuple[bool, str, dict | None, str]:
    """Generate → validate → RENDER (server) → tone-check the rendered reply. Returns
    (ok, reason, contract, rendered_text). Moderation-unavailable is an explicit
    degraded result that is bounded, not a silent fail-open."""
    try:
        contract = _generate_contract(input_list, system, corrective=corrective)
    except Exception as e:
        return False, f"contract generation failed: {type(e).__name__}", None, ""
    ok, reason = _validate_contract(contract, rec)
    if not ok:
        return False, reason, contract, ""
    rendered = _render_reply(contract, rec)
    tone = guardrails.check_tone(rendered)
    if tone["flagged"]:
        return False, "tone flagged by moderation", contract, ""
    if tone.get("degraded"):
        rec["guardrail"].append(("tone", "degraded", "moderation unavailable — output not delivered"))
        return False, "moderation unavailable — cannot verify output tone", contract, ""
    return True, "", contract, rendered


_CEILING_TEMPLATE = ("I hear you, and I want to be straight with you: the most I'm able to offer is "
                     "{label}. If that works for you, I'm glad to set it up — and if not, I completely "
                     "understand. Would you like to go ahead with it?")


def _intended_kind(contract: dict | None) -> str | None:
    """The offer kind the failed contract was trying to present (so the fallback
    targets THAT offer, not an ambiguous 'latest live' one)."""
    o = (contract or {}).get("offer") or {}
    return o.get("kind") if o.get("kind") in ("discount", "pause") else None


def _ceiling_fallback(rec: dict, kind: str | None) -> str | None:
    """Deterministic safe fallback when the model keeps trying to OVER-promise: if
    there is an authorized offer of the kind the contract was presenting, present it
    at exactly the authorized ceiling (what policy actually allows) rather than
    escalating. Only an output failure with NO offer to fall back to should hand off."""
    off = offers.offer_of_kind(rec["offers"], kind) if kind else offers.active(rec["offers"])
    if off is None:
        return None
    offers.present(rec["offers"], off, off.authorized_terms)
    return _CEILING_TEMPLATE.format(label=offers.human_terms(off, off.authorized_terms))


def _finalize_output(rec: dict, input_list: list, system: str, on_step) -> str:
    """Generate the reply as a server-authoritative contract, validate it, and return
    the SERVER-RENDERED text. On failure, regenerate ONCE; if it still fails, hold
    firm at the authorized ceiling when an offer exists, else FAIL CLOSED (safe reply
    + escalate). Nothing that fails validation is ever delivered."""
    ok, reason, contract, rendered = _screen_contract(input_list, system, rec, "")
    if ok:
        _apply_contract(contract, rec)
        _emit(on_step, "output", "Contract validated; reply rendered server-side from the ledger")
        return rendered
    rec["guardrail"].append(("output", "regenerating", reason))
    _emit(on_step, "output", f"Output contract rejected ({reason}) — regenerating once")
    ok2, reason2, contract2, rendered2 = _screen_contract(input_list, system, rec, reason)
    if ok2:
        _apply_contract(contract2, rec)
        _emit(on_step, "output", "Contract validated after one regeneration")
        return rendered2
    fallback = _ceiling_fallback(rec, _intended_kind(contract2) or _intended_kind(contract))
    if fallback is not None:
        rec["guardrail"].append(("output", "capped_to_ceiling", reason2))
        _emit(on_step, "output", "Held firm at the authorized ceiling (no over-promise delivered)")
        return fallback
    rec["guardrail"].append(("output", "blocked", reason2))
    rec["escalated"] = True
    _emit(on_step, "output", "Output contract could not be validated — escalating")
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
            # The model is ready to respond — generate the customer-facing reply as
            # a validated structured contract (not the free text, which we discard).
            text = _finalize_output(rec, input_list, system, on_step)
            input_list.append({"role": "assistant", "content": text})
            return text
        for call in calls:
            input_list.append({"type": "function_call", "call_id": call.call_id,
                               "name": call.name, "arguments": call.arguments})
            args = json.loads(call.arguments or "{}")
            result = _resolve_call(call.name, args, cid, sub, conn, rec,
                                   call_id=call.call_id, on_step=on_step)
            input_list.append({"type": "function_call_output", "call_id": call.call_id,
                               "output": json.dumps(result)})
        # An escalation resolves the turn — don't keep spinning tool hops.
        if rec["escalated"]:
            _emit(on_step, "output", "Preparing a hand-off to a human teammate")
            msg = _handoff_message(input_list, system, rec)
            input_list.append({"role": "assistant", "content": msg})
            return msg
    # MAX_HOPS exhausted without a final reply — treat it as a real escalation
    # (log it, transition state, hand off warmly), not a silent truncation.
    rec["escalated"] = True
    rec["escalate_reason"] = "tool-resolution hop limit reached without a final reply"
    rec["guardrail"].append(("max_hops", "routed", f"exceeded {MAX_HOPS} tool hops; handing off"))
    _emit(on_step, "output", "Hop limit reached — handing off to a human teammate")
    msg = _handoff_message(input_list, system, rec)
    input_list.append({"role": "assistant", "content": msg})
    return msg


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
        input_list.append({"role": "user", "content": dec["context"]})
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
        "offer_made": _offer_made(rec),
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
    rec = _new_rec()

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
        if cust["decision"] in ("accept", "reject"):
            # The customer's closing line is part of the conversation — record it so
            # the persisted transcript ends where the exchange actually ended
            # (the judge and disposition read the full turn, not a truncation).
            if cust.get("reply"):
                transcript.append({"role": "user", "content": cust["reply"]})
            if cust["decision"] == "accept":
                # A save requires an accepted *presented* offer. "Yes, cancel me"
                # with nothing on the table is the customer accepting cancellation.
                # Acceptance is bound to the specific presented offer in the ledger.
                accepted = offers.mark_accepted(rec["offers"])
                outcome, offer_accepted = ("saved", True) if accepted else ("lost", False)
            else:  # rejected — transition the presented offer to 'rejected'
                offers.mark_rejected(rec["offers"])
                outcome = "lost"
            break
        pending = cust["reply"]  # screened by _advance on the next iteration
    if outcome is None:
        outcome = "lost"

    disp = _disposition(transcript, scenario, rec, outcome, offer_accepted)
    return {"customer_id": cid, "scenario_id": scenario["id"], "transcript": transcript,
            "disposition": disp, "outcome": outcome, "offer_made": _offer_made(rec),
            "evidence": _evidence(rec), "guardrail_events": rec["guardrail"],
            "audit": rec["audit"]}


def persist_conversation(conn, record: dict) -> int:
    """Write a simulated conversation + its audit/guardrail rows. Returns the
    new conversation id and stamps it onto the record. Call on the main thread."""
    # De-identify before storage/embedding: redact EVERY role defensively (data
    # minimization). User turns are normally redacted at input, but the terminal
    # simulator reply and any assistant turn that echoes account PII must also be
    # scrubbed — persistence does not assume an upstream guarantee.
    stored_transcript = [
        {"role": t["role"], "content": guardrails.redact_pii(t["content"])[0]}
        for t in record["transcript"]
    ]
    cur = conn.execute(
        "INSERT INTO conversations "
        "(customer_id, scenario_id, transcript_json, disposition_json, offer_made, evidence_json, "
        "outcome, run_id, phase, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (record["customer_id"], record["scenario_id"], db.dumps(stored_transcript),
         db.dumps(record["disposition"]), record["offer_made"],
         db.dumps(record.get("evidence") or {}), record["outcome"],
         record.get("run_id"), record.get("phase"), _now()),
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
    rec = _new_rec()
    disc = disclosure.disclosure_message()
    state = safety.program_state(conn)  # kill switch — start in safe mode if unhealthy
    return {"customer_id": customer_id, "sub": sub, "customer": customer, "rec": rec,
            "transcript": [disc], "input_list": [disc], "outcome": None,
            "disclosure": disc["content"], "safe_mode": not state["healthy"],
            "safety_reasons": state["reasons"]}


def _turn_result(session: dict, reply: str, new_events: list) -> dict:
    rec = session["rec"]
    return {"reply": reply, "escalated": rec["escalated"], "offer_made": _offer_made(rec),
            "outcome": session["outcome"], "new_guardrail_events": new_events,
            "guardrail_events": list(rec["guardrail"])}


_ESCALATED_REPLY = ("You're already connected with a human teammate on this — they have the full "
                    "conversation and will follow up. I'll leave it in their hands.")


def live_turn(session: dict, user_text: str, conn, *, on_step=None) -> dict:
    """Advance one live turn using the SAME shared core (_advance) as the batch
    runner: a jailbreak is blocked before the model, off-scope is bounded,
    otherwise the agent runs; a needs-human or output violation escalates."""
    rec = session["rec"]
    # Escalation is a TERMINAL state: once the conversation has been handed to a
    # human, the autonomous agent does not run again on this session (mirrors the
    # batch path, which stops on escalation). No more model turns after hand-off.
    if session.get("outcome") == "escalated" or rec.get("escalated"):
        session["transcript"].append({"role": "user", "content": guardrails.redact_pii(user_text)[0]})
        session["transcript"].append({"role": "assistant", "content": _ESCALATED_REPLY})
        _emit(on_step, "guardrail", "Session already escalated — not running the agent", gtype="escalated")
        return _turn_result(session, _ESCALATED_REPLY, [])
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
    # Idempotent: a retry after a successful resolve returns the SAME record rather
    # than double-persisting or failing (the offer is already accepted/rejected).
    if session.get("resolved"):
        return session["_record"]
    if session["outcome"] == "escalated":
        outcome = "escalated"
    if outcome not in ("saved", "lost", "escalated"):
        raise ValueError("outcome must be saved, lost, or escalated")
    # Validate WITHOUT mutating the ledger yet — a save needs a presented offer.
    if outcome == "saved" and offers.presented(rec["offers"]) is None:
        raise ValueError("cannot mark 'saved' without a presented retention offer")
    offer_accepted = outcome == "saved"
    scenario = {"id": None, "customer_id": session["customer_id"], "churn_reason": "live session"}
    # Snapshot the ledger states so a persistence failure can be rolled back and the
    # resolve retried cleanly (don't leave the session half-resolved).
    snapshot = [(o, o.state) for o in rec["offers"]]
    try:
        if outcome == "saved":
            offers.mark_accepted(rec["offers"])
        elif outcome == "lost":  # a presented-but-declined offer transitions to 'rejected'
            offers.mark_rejected(rec["offers"])
        disp = _disposition(session["transcript"], scenario, rec, outcome, offer_accepted)
        record = {"customer_id": session["customer_id"], "scenario_id": None,
                  "transcript": session["transcript"], "disposition": disp, "outcome": outcome,
                  "offer_made": _offer_made(rec), "evidence": _evidence(rec),
                  "guardrail_events": rec["guardrail"], "audit": rec["audit"]}
        persist_conversation(conn, record)  # the durable commit — after this, resolved is true
    except Exception:
        for o, st in snapshot:  # roll back the in-memory transition so a retry works
            o.state = st
        raise
    session["resolved"] = True
    session["_record"] = record
    session["conversation_id"] = record["conversation_id"]
    # Grade AFTER the durable commit. A grading failure records an 'error' eval row
    # (never raises); a missed live grade is later reconciled by grade_all, which
    # grades any conversation lacking a current-spec eval. So resolve stays durable.
    _grade_and_store(conn, record["conversation_id"], record, session["customer_id"])
    return record


def _grade_and_store(conn, conv_id: int, record: dict, customer_id: int) -> None:
    """Grade a persisted conversation and write its eval row, so the live path
    grades 100% of conversations. Builds the judge input from the SAME persisted
    envelope the batch path uses (identical evidence). A judge failure writes a
    coverage-miss eval (verdict 'error') rather than silently leaving it ungraded."""
    from evals import judge, run_evals  # local import avoids any load-order cycle
    convo = run_evals.build_judge_input(conn, conv_id)
    ver = judge.EVAL_SPEC_VERSION  # SAME content-hashed spec id the batch path stamps
    # idempotent: a retry replaces this conversation's row for the current spec
    conn.execute("DELETE FROM evals WHERE conversation_id=? AND rubric_version=?", (conv_id, ver))
    try:
        v = judge.judge_conversation(convo)
        verdict = judge.derive_verdict(v["scores"])
        conn.execute(
            "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, rubric_version, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (conv_id, db.dumps(v["scores"]), verdict, v["rationale"], int(v["fairness_flag"]), ver, _now()))
    except Exception as e:
        conn.execute(
            "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, rubric_version, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (conv_id, db.dumps({}), "error", f"grading failed: {type(e).__name__}", 0, ver, _now()))
    conn.commit()
