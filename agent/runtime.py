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
import sqlite3
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
- ALWAYS attempt a retention offer before conceding a cancellation. Call offer_discount or offer_pause and present what policy authorizes FIRST; only once you've made your best offer and the customer has still declined should you process the cancellation. Never concede on the first turn without trying. If a tool tells you no offer is authorized (e.g. a save-offer cooldown), THEN acknowledge warmly and let them go gracefully. Do NOT reflexively escalate; only call escalate_to_human if the customer explicitly asks for a person or requests something consequential (a refund, a contract change) that you cannot handle.
- Hold your best offer: if the customer reacts positively to an offer ("that helps") but pushes for more than you can give, restate that SAME offer as your firm best — do not drop it and switch to a weaker one, and do not abandon it to a cancellation.
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


# Safe (non-identifying) account fields per read tool. Anything NOT listed — most
# importantly get_customer.name — is dropped from the PERSISTED eval envelope. The
# judge grades offers, account facts, and policy decisions; none of that needs raw
# customer identity, and the fields the agent can state to a customer (price, plan,
# seats, …) are all here.
_EVIDENCE_TOOL_FIELDS = {
    "get_customer": ("segment", "tenure_months", "arpu"),
    "get_subscription": ("plan", "price", "status", "last_save_offer_days"),
    "get_usage": ("logins_last_30d", "active_seats", "licensed_seats", "feature_adoption_pct"),
}

# Fields the agent may state TO THE CUSTOMER (and therefore render into a durable
# transcript). Deliberately narrower than the eval allowlist above: it excludes
# customer identity (get_customer.name — PII) and internal fields (segment, arpu, the
# cooldown counter) the customer has no business being told. A contract that references
# a (tool, field) not listed here is rejected, so a name can never reach the reply or
# the stored transcript by being cited as an "account fact".
_CUSTOMER_FACT_FIELDS = {
    "get_subscription": ("plan", "price", "status"),
    "get_usage": ("active_seats", "licensed_seats", "logins_last_30d", "feature_adoption_pct"),
}


def _redact_evidence_value(v):
    """Recursively redact free-form strings anywhere in a persisted-evidence value —
    so PII the model copied into a policy argument (a `reason`, a `new_plan`) is
    scrubbed just like the transcript is."""
    if isinstance(v, str):
        return guardrails.redact_pii(v)[0]
    if isinstance(v, dict):
        return {k: _redact_evidence_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact_evidence_value(x) for x in v]
    return v


def _deidentify_tool_fact(fct: dict) -> dict:
    """Allowlist safe fields for a read-tool result (dropping name / any un-listed
    field), then recursively redact any free-form string that remains."""
    result = fct.get("result")
    if isinstance(result, dict):
        # Fail CLOSED: an unregistered tool keeps NO fields (default ()), so adding a
        # read tool without updating the evidence map can't silently switch the privacy
        # posture from allowlist to allow-all.
        allow = _EVIDENCE_TOOL_FIELDS.get(fct.get("tool"), ())
        result = {k: result[k] for k in allow if k in result}
        result = _redact_evidence_value(result)
    return {"tool": fct.get("tool"), "call_id": fct.get("call_id"), "result": result}


def _deidentify_policy_decision(p: dict) -> dict:
    """De-identify one policy decision for the persisted envelope. The judge needs the
    tool, the action, and the ADJUSTED numeric terms (pct/months) — but a model-authored
    free-text arg (deny_refund.reason, downgrade_plan.new_plan) can carry customer PII
    that no regex reliably catches, so those string args are DROPPED structurally rather
    than trusted to redaction. The policy-generated `reason` (safe) is kept, redacted."""
    args = p.get("args")
    numeric_args = ({k: v for k, v in args.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                    if isinstance(args, dict) else {})
    return {"tool": p.get("tool"), "action": p.get("action"),
            "args": numeric_args, "reason": _redact_evidence_value(p.get("reason"))}


def _evidence(rec: dict) -> dict:
    """The canonical eval envelope, DE-IDENTIFIED for persistence. It keeps everything
    the batch and live judge need to grade identically — the full offer ledger (exact
    authorized + presented terms), the read-tool facts (for grounding), and the policy
    decisions with adjusted NUMERIC args — but strips customer identity BEFORE it is
    stored: per-tool safe-field allowlisting drops names, model-authored free-text policy
    args are dropped, and every remaining free-form string is recursively redacted. Raw
    tool data stays in the in-memory `rec` for the live turn; only this view is persisted."""
    return {"offers": [o.to_dict() for o in rec["offers"]],
            "tool_facts": [_deidentify_tool_fact(f) for f in rec["tool_facts"]],
            "policy_decisions": [_deidentify_policy_decision(p) for p in rec["policy_decisions"]]}


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
        # Authorize into the ledger as a CANDIDATE. Several offers may be authorized
        # during a negotiation; exactly one is ever PRESENTED (enforced at present time).
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


# Hand-off messages are SERVER-TEMPLATED, keyed by WHY we're escalating. A hand-off
# is a routing message, so — like the rest of the customer-facing reply — no model
# authors its words: it states only that a teammate is taking over, never an offer,
# an account fact, or a completed action. (Previously the model wrote the hand-off
# and a regex screened it; a regex can't prove free text carries no fabricated claim,
# so the free text is gone.)
_HANDOFF_TEMPLATES = {
    "refund": ("I understand you're looking for a refund. That needs a teammate with the right "
               "authority, so I'm bringing one in now — they'll have our full conversation, so you "
               "won't need to repeat anything."),
    "human": ("Of course — I'm connecting you with a teammate now. They'll have everything we've "
              "discussed, so you can pick up right where we left off."),
    "consequential": ("This one needs a teammate who can make that change for you. I'm handing it to "
                      "them now with our full conversation, so nothing gets lost."),
    "generic": ("Understood — I'm bringing in a teammate who can help with this. They'll have our full "
                "conversation, so you won't need to repeat anything."),
}
_HANDOFF_FALLBACK = _HANDOFF_TEMPLATES["generic"]


def _handoff_safe(text: str, rec: dict | None = None) -> bool:
    """Invariant every hand-off message must satisfy: NO offer, NO dollar amount, NO
    completed-action claim — it only routes to a human. The server templates satisfy
    this by construction (a test asserts it); the helper stays as the checkable
    property, not as a screen over model-authored text."""
    if not text:
        return False
    if guardrails.extract_discount_pcts(text) or guardrails.extract_pause_months(text):
        return False  # a hand-off must not float an offer
    if _MONEY_IN_TEXT.search(text):
        return False  # …nor state a dollar figure
    return not guardrails.check_completion_claim(text)


def _handoff_reason_key(rec: dict) -> str:
    """Choose the hand-off template matching the escalation reason: an explicit refund
    request, an explicit ask for a person, another consequential change, or generic."""
    blob = (str(rec.get("escalate_reason", "")) + " "
            + " ".join(str(e) for e in rec.get("guardrail", []))).lower()
    if "refund" in blob:
        return "refund"
    if any(w in blob for w in ("human", "person", "representative", "someone", "manager")):
        return "human"
    if any(w in blob for w in ("consequential", "contract", "cancel_contract", "legal")):
        return "consequential"
    return "generic"


def _handoff_message(input_list: list, system: str, rec: dict | None = None) -> str:
    """The closing message when the agent must escalate — SERVER-TEMPLATED (no model
    call), keyed by the escalation reason. A hand-off never carries an offer, an
    account fact, or a completed action; it only routes to a human."""
    rec = rec if rec is not None else _new_rec()
    return _HANDOFF_TEMPLATES.get(_handoff_reason_key(rec), _HANDOFF_FALLBACK)


_OUTPUT_SAFE_REPLY = ("I want to make sure I give you accurate, approved information — "
                      "let me bring in a teammate to help with this.")

# The set of acknowledgement INTENTS the model may pick. It never writes the words —
# it chooses the feeling, the server renders the sentence. Keyed to the churn drivers
# in the synthetic data plus a generic and a wrap-up ('closing').
_ACK_TEMPLATES = {
    # opening acknowledgements — keyed to why the customer is leaving
    "price": "I completely understand — cost has to make sense, and I'd like to find a way to make this work for you.",
    "missing_feature": "That's fair — if something your team relies on isn't supported yet, that's a real gap, and I appreciate you telling us.",
    "competitor": "I understand, and I respect that you're weighing your options — I'd genuinely like the chance to keep you with us.",
    "support": "I'm sorry the support experience let you down — that's on us, and I'd like to make it right.",
    "no_longer_needed": "That's completely fair — if it isn't part of your workflow anymore, I want to respect that.",
    "generic": "I hear you, and I want to make sure we handle this the way that's right for you.",
    # resolution closes — how to end cleanly so a conversation doesn't dead-end. Each
    # is only VALID in a matching ledger state (see _validate_contract), so the server
    # can't author a "we sorted it out" that never happened.
    "cant_meet": "I want to be straight with you: I've offered the most I'm able to here, and I can't stretch further than that on price.",
    "letting_go": "I completely understand, and I won't stand in your way here.",
    "closing": "Thanks — I'm glad we could sort that out, and I appreciate you sticking with us.",
}

# Acknowledgements that CLOSE a cancellation (state a wind-down, not a save). A
# process_cancellation contract must use one of these — never an opening/negotiation
# intent (which would leave the close ungrounded) and never 'closing' (which claims a
# save). ('cant_meet' requires an offer was actually presented; 'letting_go' is the
# neutral close. An earlier 'offer_declined' intent was removed: it required a ledger
# 'rejected' state that only ever exists at a terminal point, so it was unreachable
# during an agent turn and merely forced wasted regenerations.)
_CANCELLATION_ACKS = frozenset({"cant_meet", "letting_go"})

# SERVER-AUTHORITATIVE OUTPUT. The model authors NO customer-facing text at all. It
# returns only STRUCTURED intent — an acknowledgement to convey (chosen from a fixed
# set), an offer by kind+terms, and account facts by field+source-tool — and the
# server renders every sentence itself from validated data. There is no free-form
# channel in which the model could state an account fact, a capability, a refund, or a
# cancellation it isn't entitled to. (Earlier the model wrote 'fact-free' empathy prose
# that a regex tried to police; a growing regex can't prove prose is fact-free — so the
# prose is gone, replaced by an enum.)
_CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "acknowledgement": {
            "type": "string",
            "enum": list(_ACK_TEMPLATES),
            "description": ("The feeling to acknowledge — the SERVER renders the sentence, you only pick "
                            "the intent. While negotiating, match why they're leaving (price, "
                            "missing_feature, competitor, support, no_longer_needed) or 'generic'. To "
                            "CLOSE a conversation you can't save, pick a resolution intent so it ends "
                            "cleanly: 'cant_meet' when you've made an offer and can't go further, "
                            "'letting_go' to let them go neutrally, 'closing' after a successful save. "
                            "Never leave a conversation hanging on an opening intent."),
        },
        "offer": {
            "type": "object",
            "description": "The single retention offer to present, or kind='none'.",
            "properties": {
                "kind": {"type": "string", "enum": ["discount", "pause", "none"]},
                "pct": {"type": ["integer", "null"], "description": "Discount percent, a WHOLE number (kind=discount), else null."},
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
    "required": ["acknowledgement", "offer", "account_facts", "process_cancellation"],
    "additionalProperties": False,
}

_CONTRACT_INSTRUCTIONS = (
    "\n\nProduce your reply as a SERVER-RENDERED contract. You do NOT write the customer-facing text at "
    "all — you choose intent and the system renders every word:\n"
    "- acknowledgement: pick the feeling to convey from the allowed set (the server writes the sentence). "
    "While negotiating, match why they're leaving. When you cannot save them, CLOSE cleanly with a "
    "resolution intent — 'cant_meet' if you made an offer and can't go further, 'letting_go' to let them go "
    "neutrally, 'closing' after a save — so the conversation never dead-ends.\n"
    "- offer: the ONE retention offer to present, as kind + exact terms, or kind='none'. Terms must not "
    "exceed what the tool results authorized (if a tool authorized 'up to 20%', use at most 20 — never more, "
    "even if the customer demands it). Never reference an offer no tool authorized.\n"
    "  RESOLUTION RULES: (1) if you have an authorized offer the customer hasn't seen yet, PRESENT it — do "
    "not set process_cancellation while a real offer is still on the table. (2) Never re-offer something the "
    "customer already declined; if your best offer was refused and you have nothing else authorized, set "
    "offer.kind='none', pick 'cant_meet' (or 'letting_go'), and process_cancellation=true to close. "
    "(3) Make your best offer ONCE, then respect a firm no — looping the same offer is a failure to resolve.\n"
    "- account_facts: leave EMPTY unless the customer explicitly asked about a specific account fact; then "
    "reference it by field + the tool that returned it (the system fills in the value). ONLY these "
    "customer-visible fields are allowed: plan, price, status (get_subscription) and active_seats, "
    "licensed_seats, logins_last_30d, feature_adoption_pct (get_usage). NEVER reference the customer's name "
    "or any internal field — those are not customer-facing. Never volunteer account facts.\n"
    "- process_cancellation: true when you are letting the customer go (pair it with 'cant_meet'/"
    "'letting_go', never with an offer still being presented).\n"
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
    """Validate the SERVER-AUTHORITATIVE contract. The model authors NO customer-facing
    text — only structured intent — so there is no prose to police for smuggled facts.
    We check: the acknowledgement is a known intent, the offer is an authorized ledger
    offer within its ceiling, and each account_fact references a field a cited tool
    actually returned. Returns (ok, reason)."""
    # 1) the acknowledgement must be one the SERVER knows how to render (the enum is
    #    already enforced by structured output; this is the defensive backstop).
    ack = contract.get("acknowledgement")
    if ack not in _ACK_TEMPLATES:
        return False, f"unknown acknowledgement intent {ack!r}"

    # 1b) STATE-GROUNDED acknowledgements — the chosen intent must be TRUE for the
    #     current ledger state, so the SERVER can't author a claim that never happened
    #     (a "we sorted it out" with no save, "you declined" with no rejected offer).
    states = {o.state for o in rec["offers"]}
    extended = states & {"presented", "accepted", "rejected"}  # an offer was actually made
    if ack == "closing" and "accepted" not in states:
        return False, "acknowledgement 'closing' claims a save, but no offer was accepted"
    if ack == "cant_meet" and not extended:
        return False, "acknowledgement 'cant_meet' claims an offer was made, but none was presented"

    # 2) the offer must be an authorized ledger offer of its kind, within the ceiling
    offer = contract.get("offer") or {"kind": "none"}
    kind = offer.get("kind")

    # 2a) a cancellation must CLOSE with a resolution intent (never an opening/negotiation
    #     acknowledgement, never 'closing' which claims a save) and present no offer.
    if contract.get("process_cancellation"):
        if kind != "none":
            return False, "cannot process a cancellation while presenting an offer"
        if ack not in _CANCELLATION_ACKS:
            return False, (f"a cancellation must close with a resolution intent "
                           f"{sorted(_CANCELLATION_ACKS)}, not '{ack}'")
        # 2c) don't CONCEDE prematurely — a retention agent attempts an offer before it
        #     routes a cancellation. If none was authorized AND none was even attempted
        #     (no offer tool was called), the agent simply hasn't tried: make it try first.
        attempted = bool(rec["offers"]) or any(
            p.get("tool") in ("offer_discount", "offer_pause") for p in rec.get("policy_decisions", []))
        if not attempted:
            return False, "attempt a retention offer before conceding a cancellation — you haven't tried one yet"
    if kind in ("discount", "pause"):
        terms = {"pct": offer.get("pct")} if kind == "discount" else {"months": offer.get("months")}
        if (kind == "discount" and offer.get("pct") is None) or (kind == "pause" and offer.get("months") is None):
            return False, "offer is missing its terms"
        # resolution guard A — don't loop: never re-offer a kind the customer already declined
        if offers.rejected_of_kind(rec["offers"], kind) is not None:
            return False, (f"the customer already declined a {kind} — do not re-offer it; make another "
                           f"authorized offer or close gracefully (cant_meet / letting_go)")
        match = offers.offer_of_kind(rec["offers"], kind)
        if match is None:
            return False, f"offer presents a {kind} that policy did not authorize"
        if not offers.terms_within(terms, match.authorized_terms, kind):
            return False, f"offer {terms} exceeds the authorized ceiling {match.authorized_terms}"

    # resolution guard B — don't abandon: never process a cancellation while a fresh
    # authorized offer the customer hasn't seen is still on the table
    if kind == "none" and contract.get("process_cancellation"):
        pending = offers.unpresented_new_authorized(rec["offers"])
        if pending is not None:
            return False, (f"you have an authorized {pending.kind} the customer hasn't seen — present it "
                           f"before processing a cancellation")

    # 3) each account_fact must (a) be a CUSTOMER-VISIBLE field (never identity/PII or an
    #    internal field), and (b) reference a value a cited tool actually returned.
    for f in contract.get("account_facts", []):
        field, tool = f.get("field"), f.get("source_tool")
        if field not in _CUSTOMER_FACT_FIELDS.get(tool, ()):
            return False, (f"account_fact '{field}' from '{tool}' is not a customer-visible field "
                           f"(identity/internal fields must never be stated to the customer)")
        if _fact_value(rec, field, tool) is None:
            return False, (f"account_fact '{field}' from '{tool}' was not returned by that tool")
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
        return f"I can apply {int(terms['pct'])}% off your subscription going forward."
    if kind == "pause":
        return f"I can set up a {int(terms['months'])}-month pause on your plan, so you won't be billed during it."
    return ""


_DOLLAR_FIELDS = ("price", "amount", "balance", "credit", "total", "fee")


def _fact_sentence(field: str, value) -> str:
    label = field.replace("_", " ")
    if field in _DOLLAR_FIELDS and isinstance(value, (int, float)):
        return f"Your {label} is ${float(value):.2f}."
    return f"Your {label} is {value}."


# Cancellation is routed to a human, stated honestly: this POC has no billing backend,
# so the reply does NOT assert the cancellation is done or claim a billing-period fact
# it can't back — it says a teammate will process it and confirm.
_CANCELLATION_SENTENCE = ("I'll pass your cancellation to our team to process, and we'll email you a "
                          "confirmation once it's complete.")


def _render_reply(contract: dict, rec: dict) -> str:
    """Assemble the customer-facing reply ENTIRELY from server text: the acknowledgement
    sentence (rendered from the chosen intent), then every factual sentence the server
    renders from validated data (account facts, the offer, the cancellation). No part
    of this string is authored by the model."""
    parts = [_ACK_TEMPLATES.get(contract.get("acknowledgement"), _ACK_TEMPLATES["generic"])]
    for f in contract.get("account_facts", []):
        # defense in depth: never render a field that isn't customer-visible, even if a
        # contract somehow reached here with one (validation already rejects them).
        if f.get("field") not in _CUSTOMER_FACT_FIELDS.get(f.get("source_tool"), ()):
            continue
        val = _fact_value(rec, f.get("field"), f.get("source_tool"))
        if val is not None:
            parts.append(_fact_sentence(f["field"], val))
    offer = contract.get("offer") or {"kind": "none"}
    if offer.get("kind") in ("discount", "pause"):
        terms = {"pct": offer.get("pct")} if offer["kind"] == "discount" else {"months": offer.get("months")}
        parts.append(_offer_sentence(offer["kind"], terms))
        parts.append("Would you like to go ahead with that, or should I process the cancellation?")
    elif contract.get("process_cancellation"):
        parts.append(_CANCELLATION_SENTENCE)
    return " ".join(p for p in parts if p)


def _route_cancellation(rec: dict) -> None:
    """A validated cancellation is a REAL, recorded action — not just a sentence. It
    writes a durable audit entry and flags the session terminal (so no further
    autonomous turns run and persistence enqueues a mock work-queue item). This is what
    makes the customer-facing 'a teammate will process this' true: state records the
    obligation, rather than the server authoring an empty promise."""
    if rec.get("cancellation_routed"):
        return
    rec["cancellation_routed"] = True
    rec["audit"].append(("agent", "cancellation_routed",
                         "cancellation queued for human processing (mock work queue), email follow-up"))


def _queue_cancellation_live(conn, session_key: str | None) -> None:
    """Durably enqueue a cancellation work item keyed by SESSION id BEFORE the live
    routing sentence is returned — so the promise survives a browser close, restart, or
    TTL eviction even if /resolve is never called. Idempotent on the session key; the
    row is linked to its conversation later at persist time."""
    if not session_key:
        return
    conn.execute(
        "INSERT OR IGNORE INTO cancellation_requests (session_key, status, channel, created_at) "
        "VALUES (?,?,?,?)", (session_key, "pending_human", "email", _now()))
    conn.commit()


def _queue_escalation_live(conn, session_key: str | None, reason: str | None) -> None:
    """Durably enqueue a human hand-off keyed by SESSION id at turn time — the same
    durability the cancellation path has (H4): the promise 'a teammate will take it from
    here' survives a browser close, restart, or TTL eviction even if /resolve never
    comes. Idempotent on the session key; linked to its conversation at persist."""
    if not session_key:
        return
    # The reason is MODEL-authored free text and can carry customer PII no regex reliably
    # catches — redact it before it lands in a durable store, exactly as the transcript,
    # churn_reason, and evidence envelope are scrubbed on their durable write paths.
    safe_reason = guardrails.redact_pii(reason)[0][:200] if reason else None
    conn.execute(
        "INSERT OR IGNORE INTO escalation_requests (session_key, reason, status, created_at) "
        "VALUES (?,?,?,?)", (session_key, safe_reason, "pending_human", _now()))
    conn.commit()


def _apply_contract(contract: dict, rec: dict) -> None:
    """Apply a validated contract to conversation state: mark the offered ledger entry
    presented with the exact offered terms (so outcome/economics use presented, not
    authorized, terms), and route a cancellation as a real terminal action."""
    if contract.get("process_cancellation"):
        _route_cancellation(rec)
    offer = contract.get("offer") or {"kind": "none"}
    kind = offer.get("kind")
    if kind not in ("discount", "pause"):
        return
    terms = {"pct": int(offer["pct"])} if kind == "discount" else {"months": int(offer["months"])}
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
    """Deterministic safe fallback when the model keeps trying to OVER-promise: if the
    failed contract was presenting a SPECIFIC kind and policy authorized that kind,
    present it at exactly the authorized ceiling rather than escalating. With NO
    intended kind there is no principled way to choose among multiple authorized
    candidates — picking by recency would be arbitrary — so we return None and let the
    caller escalate. Multiple offers may be AUTHORIZED; exactly one is ever PRESENTED,
    and only a kind the model actually tried to present is a valid fallback target."""
    if kind is None:
        return None
    off = offers.offer_of_kind(rec["offers"], kind)
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
    # present-before-abandon: if the model kept trying to cancel while a fresh authorized
    # offer was still on the table, present it deterministically rather than escalating —
    # but ONLY when there is exactly ONE candidate. With several un-declined offers there
    # is no principled way to choose (tool-call order is not product strategy), so we
    # escalate rather than present one by recency.
    pending = offers.unpresented_candidates(rec["offers"])
    if len(pending) == 1:
        off = pending[0]
        offers.present(rec["offers"], off, off.authorized_terms)
        rec["guardrail"].append(("output", "presented_before_abandon", reason2))
        _emit(on_step, "output", "Presented the single authorized offer before any cancellation")
        return _CEILING_TEMPLATE.format(label=offers.human_terms(off, off.authorized_terms))
    rec["guardrail"].append(("output", "blocked", reason2))
    rec["escalated"] = True
    _emit(on_step, "output", "Output contract could not be validated — escalating")
    return _OUTPUT_SAFE_REPLY


def _safe_resolve_call(call, cid: int, sub: dict, conn, rec: dict, *, on_step=None) -> dict:
    """Parse tool arguments defensively and resolve the call, converting ANY malformed
    input or execution error into a fail-closed error tool result (logged as a guardrail
    event) instead of raising out of the turn. Strict schemas are the first gate; this
    is the belt-and-suspenders second one."""
    try:
        args = json.loads(call.arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments were not a JSON object")
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        rec["guardrail"].append(("tool_args", "rejected", f"{call.name}: {type(e).__name__}"))
        return {"status": "error", "reason": f"invalid tool arguments ({type(e).__name__})"}
    try:
        return _resolve_call(call.name, args, cid, sub, conn, rec, call_id=call.call_id, on_step=on_step)
    except Exception as e:  # a bad value (e.g. non-numeric months) must not crash the turn
        rec["guardrail"].append(("tool_args", "rejected", f"{call.name}: {type(e).__name__}"))
        return {"status": "error", "reason": f"tool could not run ({type(e).__name__})"}


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
            # The model is an untrusted proposer: malformed JSON or a bad argument must
            # become a FAIL-CLOSED tool result the model can recover from, never an
            # unhandled worker error before the deterministic policy layer runs.
            result = _safe_resolve_call(call, cid, sub, conn, rec, on_step=on_step)
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
        # A routed cancellation is TERMINAL in BOTH paths (one state machine, no drift):
        # once the agent has conceded and recorded the action, it does not run again, so a
        # conversation can never be both saved and cancellation-routed. Guard C ('offer
        # before you concede') + the hold-your-offer prompt mean genuine saves land BEFORE
        # any concession, so terminality doesn't cost recoverable saves.
        if rec.get("cancellation_routed"):
            outcome = "lost"
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
            "audit": rec["audit"], "cancellation_routed": rec.get("cancellation_routed", False),
            # so a batch-persisted escalation carries its reason (parity with the live path)
            "escalate_reason": rec.get("escalate_reason")}


def persist_conversation(conn, record: dict) -> int:
    """Write a simulated conversation + its audit/guardrail rows. Returns the
    new conversation id and stamps it onto the record. Call on the main thread."""
    # Application invariant: a routed cancellation is terminal, so 'saved' and
    # cancellation_routed are mutually exclusive. Refuse to persist the contradiction
    # rather than let a logically impossible record reach the metrics or the judge.
    if record.get("outcome") == "saved" and record.get("cancellation_routed"):
        raise ValueError("invariant violation: a conversation cannot be both saved and cancellation-routed")
    # De-identify before storage/embedding: redact EVERY role defensively (data
    # minimization). User turns are normally redacted at input, but the terminal
    # simulator reply and any assistant turn that echoes account PII must also be
    # scrubbed — persistence does not assume an upstream guarantee.
    stored_transcript = [
        {"role": t["role"], "content": guardrails.redact_pii(t["content"])[0]}
        for t in record["transcript"]
    ]
    # disposition_json is a THIRD durable store of free text: intent/churn_reason are
    # MINI_MODEL output that can echo customer PII — scrub them like the transcript.
    stored_disposition = dict(record["disposition"])
    for _k in ("intent", "churn_reason"):
        if isinstance(stored_disposition.get(_k), str):
            stored_disposition[_k] = guardrails.redact_pii(stored_disposition[_k])[0]
    # ONE transaction for the whole conversation + its audit/guardrail rows + cooldown
    # update. If any write fails, roll the WHOLE thing back so the connection is never
    # left holding a partial conversation that a later commit could silently flush.
    # Snapshot the subscription price AT conversation time, so historical margin metrics
    # never move when a customer's current price later changes (L1). Fall back to the
    # current price only if the caller didn't provide one.
    price_at_conv = record.get("price_at_conversation")
    if price_at_conv is None:
        row = conn.execute("SELECT price FROM subscriptions WHERE customer_id=?",
                           (record["customer_id"],)).fetchone()
        price_at_conv = row["price"] if row else None
    try:
        cur = conn.execute(
            "INSERT INTO conversations "
            "(customer_id, scenario_id, transcript_json, disposition_json, offer_made, evidence_json, "
            "outcome, run_id, phase, resolution_key, price_at_conversation, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["customer_id"], record["scenario_id"], db.dumps(stored_transcript),
             db.dumps(stored_disposition), record["offer_made"],
             db.dumps(record.get("evidence") or {}), record["outcome"],
             record.get("run_id"), record.get("phase"), record.get("resolution_key"), price_at_conv, _now()),
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
        # A routed cancellation has a durable mock work-queue item. On the LIVE path a
        # session-keyed row was already enqueued at turn time — link it to this
        # conversation. On the BATCH path (or if none was pre-queued) insert one now.
        if record.get("cancellation_routed"):
            skey = record.get("resolution_key")
            linked = conn.execute(
                "UPDATE cancellation_requests SET conversation_id=? WHERE session_key=? AND conversation_id IS NULL",
                (conv_id, skey)).rowcount if skey else 0
            if not linked:
                conn.execute(
                    "INSERT INTO cancellation_requests (conversation_id, status, channel, created_at) "
                    "VALUES (?,?,?,?)", (conv_id, "pending_human", "email", _now()))
        # An escalation hand-off likewise has a durable mock queue item — link the
        # session-keyed row enqueued live, or insert one for the batch path.
        if record.get("outcome") == "escalated":
            skey = record.get("resolution_key")
            linked = conn.execute(
                "UPDATE escalation_requests SET conversation_id=? WHERE session_key=? AND conversation_id IS NULL",
                (conv_id, skey)).rowcount if skey else 0
            if not linked:
                _er = record.get("escalate_reason")
                _er = guardrails.redact_pii(_er)[0][:200] if _er else None  # model free-text → redact
                conn.execute(
                    "INSERT INTO escalation_requests (conversation_id, reason, status, created_at) "
                    "VALUES (?,?,?,?)", (conv_id, _er, "pending_human", _now()))
        # Persist cooldown state: extending a save offer starts the 90-day clock, so a
        # later conversation for the same customer won't immediately offer again.
        if record.get("offer_made"):
            conn.execute("UPDATE subscriptions SET last_save_offer_days = 0 WHERE customer_id = ?",
                         (record["customer_id"],))
        conn.commit()
    except Exception:
        conn.rollback()  # discard the partial write — never leave it open on the connection
        raise
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
_CANCELLED_REPLY = ("Your cancellation is already with our team to process — you'll get an email "
                    "confirmation once it's complete. I'll leave it in their hands.")


def live_turn(session: dict, user_text: str, conn, *, on_step=None) -> dict:
    """Advance one live turn using the SAME shared core (_advance) as the batch
    runner: a jailbreak is blocked before the model, off-scope is bounded,
    otherwise the agent runs; a needs-human or output violation escalates."""
    rec = session["rec"]
    # Escalation is a TERMINAL state: once the conversation has been handed to a
    # human, the autonomous agent does not run again on this session (mirrors the
    # batch path, which stops on escalation). No more model turns after hand-off.
    skey = session.get("_session_id")
    if session.get("outcome") == "escalated" or rec.get("escalated"):
        _queue_escalation_live(conn, skey, rec.get("escalate_reason"))  # self-heal a failed prior write
        session["transcript"].append({"role": "user", "content": guardrails.redact_pii(user_text)[0]})
        session["transcript"].append({"role": "assistant", "content": _ESCALATED_REPLY})
        _emit(on_step, "guardrail", "Session already escalated — not running the agent", gtype="escalated")
        return _turn_result(session, _ESCALATED_REPLY, [])
    # A routed cancellation is TERMINAL too — the agent has recorded the action, so a
    # later message does NOT silently re-enter the autonomous agent.
    if session.get("outcome") == "cancelled" or rec.get("cancellation_routed"):
        _queue_cancellation_live(conn, skey)  # idempotent self-heal (a prior write may have failed)
        session["outcome"] = "cancelled"
        session["transcript"].append({"role": "user", "content": guardrails.redact_pii(user_text)[0]})
        session["transcript"].append({"role": "assistant", "content": _CANCELLED_REPLY})
        _emit(on_step, "guardrail", "Cancellation already routed — not running the agent", gtype="cancelled")
        return _turn_result(session, _CANCELLED_REPLY, [])
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
        # Durably record the hand-off obligation NOW, before returning the promise.
        _queue_escalation_live(conn, skey, ev[2])
        session["outcome"] = "escalated"
        _emit(on_step, "reply", reply)
        return _turn_result(session, reply, [ev])
    before = len(rec["guardrail"])
    reply = _advance(user_text, session["transcript"], session["input_list"],
                     session["customer_id"], session["sub"], conn, rec, on_step=on_step)
    if rec["escalated"]:
        # Durably enqueue the hand-off NOW (keyed by session id), before the customer
        # receives the promise — parallel to the cancellation path; survives no /resolve.
        _queue_escalation_live(conn, skey, rec.get("escalate_reason"))
        session["outcome"] = "escalated"
    elif rec.get("cancellation_routed"):
        # Durably enqueue the work item BEFORE marking the session terminal, so a commit
        # failure can't leave a terminal session promising an obligation in no table.
        _queue_cancellation_live(conn, skey)
        session["outcome"] = "cancelled"  # terminal — the agent recorded the cancellation
    return _turn_result(session, reply, rec["guardrail"][before:])


def _load_resolved_record(conn, resolution_key: str | None) -> dict | None:
    """Load the durably-persisted record for a resolution key, or None. This is the
    DB-level idempotency source of truth: it survives a fresh session object or a
    server restart, unlike the in-memory 'resolved' flag."""
    if resolution_key is None:
        return None
    row = conn.execute(
        "SELECT id, outcome, disposition_json FROM conversations WHERE resolution_key=?",
        (resolution_key,)).fetchone()
    if row is None:
        return None
    return {"conversation_id": row["id"], "outcome": row["outcome"],
            "disposition": json.loads(row["disposition_json"])}


def resolve_session(session: dict, outcome: str, conn, *, resolution_key: str | None = None) -> dict:
    """End a live conversation: VALIDATE the outcome, persist it, and GRADE it (so the
    live path also grades 100%). Idempotent at TWO levels: the in-memory 'resolved'
    flag AND a durable `resolution_key` uniquely indexed in the DB — a retry (even from
    a fresh session object after a restart) returns the already-persisted record instead
    of double-committing. Raises ValueError only on a genuinely invalid transition."""
    rec = session["rec"]
    if session.get("resolved"):
        return session["_record"]
    # Durable idempotency: if this key already produced a conversation, return it.
    existing = _load_resolved_record(conn, resolution_key)
    if existing is not None:
        session.update(resolved=True, _record=existing, conversation_id=existing["conversation_id"])
        return existing
    if session["outcome"] == "escalated":
        outcome = "escalated"
    elif session.get("outcome") == "cancelled" or rec.get("cancellation_routed"):
        outcome = "lost"  # a routed cancellation resolves as a lost customer
    if outcome not in ("saved", "lost", "escalated"):
        raise ValueError("outcome must be saved, lost, or escalated")
    # Validate WITHOUT mutating the ledger yet. Each terminal must be EARNED, not merely
    # asserted by the caller — the loop is the source of truth, mirroring the batch path:
    # a save needs a presented offer, and an escalation needs an actual hand-off in the
    # record (so a stray outcome='escalated' can't fabricate a compliance/safety KPI).
    if outcome == "saved" and offers.presented(rec["offers"]) is None:
        raise ValueError("cannot mark 'saved' without a presented retention offer")
    if outcome == "escalated" and not (rec.get("escalated") or session.get("outcome") == "escalated"):
        raise ValueError("cannot mark 'escalated' without an actual hand-off (rec['escalated'] is not set)")
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
                  "guardrail_events": rec["guardrail"], "audit": rec["audit"],
                  "resolution_key": resolution_key,
                  "cancellation_routed": rec.get("cancellation_routed", False)}
        persist_conversation(conn, record)  # transactional durable commit (rolls back on failure)
    except sqlite3.IntegrityError:
        # Lost a race — another resolve for this SAME key committed first. Roll back our
        # in-memory transition and return the durably-persisted winner (idempotent).
        for o, st in snapshot:
            o.state = st
        winner = _load_resolved_record(conn, resolution_key)
        if winner is not None:
            session.update(resolved=True, _record=winner, conversation_id=winner["conversation_id"])
            return winner
        raise
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
    ver = judge.EVAL_SPEC_VERSION  # SAME content-hashed spec id the batch path stamps
    # idempotent: a retry replaces this conversation's row for the current spec
    conn.execute("DELETE FROM evals WHERE conversation_id=? AND rubric_version=?", (conv_id, ver))
    try:
        # build_judge_input is INSIDE the grading boundary: an envelope/format failure
        # records a coverage-miss 'error' row like any judge failure, rather than raising
        # out of resolve_session after the conversation is already durably committed.
        convo = run_evals.build_judge_input(conn, conv_id)
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
