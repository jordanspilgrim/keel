"""Conversation runtime — the agent loop (plan §3a).

OpenAI Responses API + tool calling, wrapped by the guardrail/policy layers.
Drives one retention conversation from a scenario to a structured disposition.

Flow per turn (plan §4 defense-in-depth):
  1. disclosure.disclosure_message() is the first assistant turn (Art. 50).
  2. input guardrails (jailbreak, scope, PII redaction) on each user message.
  3. model proposes tool calls; policy.authorize() disposes each.
  4. output guardrails (grounding, promise, tone) before the reply is sent.
  5. final turn emits the structured disposition via JSON schema:
       {intent, churn_reason, offer_made, offer_accepted, outcome, confidence}

Phase 1 builds the loop + disposition + policy wiring; Phase 2 adds the
guardrail calls. Model IDs come from config (FLAGSHIP_MODEL). Loads .env so the
OPENAI_API_KEY is available.
"""

from __future__ import annotations

import config

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


def run_conversation(scenario: dict, *, model: str = config.FLAGSHIP_MODEL) -> dict:
    """Run one conversation to completion. Returns {transcript, disposition,
    outcome, guardrail_events}. Persisted by the caller (run_demo.py)."""
    raise NotImplementedError("runtime.run_conversation — Phase 1")
