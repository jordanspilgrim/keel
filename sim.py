"""Simulated customer — role-plays a churning customer to drive conversations.

Part of the synthetic engine (plan §3e): given a scenario's persona + churn
reason, it reacts to the agent's latest message and returns a short reply plus a
decision signal the runtime uses to resolve the conversation. Uses the cheap
MINI_MODEL. Not the agent — this is test infrastructure that gives the agent
someone to negotiate with.

Decision ∈ {accept, reject, continue}:
  accept   → the customer takes the offer on the table (conversation → saved)
  reject   → the customer refuses and is leaving (→ lost, if the agent is out of moves)
  continue → still talking; wants a better offer or more info
"""

from __future__ import annotations

import config
import llm

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "The customer's next message (1-2 sentences)."},
        "decision": {"type": "string", "enum": ["accept", "reject", "continue"]},
    },
    "required": ["reply", "decision"],
    "additionalProperties": False,
}

_INSTRUCTIONS = """You are role-playing a customer who contacted support to CANCEL a subscription.
Stay in character. You are not an assistant; you are the customer.

Your situation:
- Reason you want to cancel: {reason}
- Your temperament: {persona}

Rules:
- React only to what the agent just said. Keep replies to 1-2 sentences.
- If the agent makes a concrete retention offer that genuinely addresses your reason and feels fair, you may 'accept'.
- IMPORTANT: if your reply says yes / "I'll take it" / "go ahead and proceed" to an offer on the table, you MUST set decision='accept' — even if you also add a follow-up question. A trailing question does not change acceptance to 'continue'.
- If the agent has no real offer for you and you just want out, set decision='reject'.
- A price-sensitive customer is swayed by a real discount or a pause; a "switched to competitor" customer is hard to keep; a "no longer needed" customer rarely stays; a "missing integration" customer only stays if the gap is addressed.
- If the agent offers nothing concrete, is vague, or only an unhelpful option, lean 'reject'.
- If you want to hear more or push for a better deal, 'continue'.
- Never break character or mention that you are an AI."""


def respond(scenario: dict, agent_message: str, history: list[dict]) -> dict:
    """Return {reply, decision} for the customer's next turn."""
    convo = "\n".join(f"{t['role']}: {t['content']}" for t in history[-6:])
    user = f"Conversation so far:\n{convo}\n\nAgent just said: {agent_message}\n\nYour reply:"
    return llm.structured(
        model=config.MINI_MODEL,
        instructions=_INSTRUCTIONS.format(reason=scenario.get("churn_reason", "unspecified"),
                                          persona=scenario.get("persona", "neutral")),
        user_input=user,
        schema=_DECISION_SCHEMA,
        name="customer_turn",
        reasoning_effort="minimal",
        max_output_tokens=500,
    )
