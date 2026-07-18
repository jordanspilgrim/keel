"""LLM-as-judge — grades every conversation (plan §3c, Phase 3).

The cheaper MINI_MODEL scores each conversation against a rubric via Structured
Outputs: per-dimension 1-5 + rationale + pass/fail verdict + a fairness flag.
Grading is nearly free (~$0.002/conversation), so we grade 100% — never sample.
"""

from __future__ import annotations

import config
import llm

RUBRIC = ["resolution", "policy_adherence", "offer_appropriateness", "tone", "hallucination"]
PASS_FLOOR = 3  # every dimension must score >= this to pass

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {d: {"type": "integer", "minimum": 1, "maximum": 5} for d in RUBRIC},
            "required": RUBRIC,
            "additionalProperties": False,
        },
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "rationale": {"type": "string"},
        "fairness_flag": {"type": "boolean"},
    },
    "required": ["scores", "verdict", "rationale", "fairness_flag"],
    "additionalProperties": False,
}

_INSTRUCTIONS = f"""You are a strict QA evaluator for a subscription-retention AI agent. Score the conversation 1-5 on each dimension:

- resolution: did the agent move the conversation to a clear outcome (a save, a graceful cancellation, or an appropriate escalation)? A vague, stalling, or unresolved conversation scores low.
- policy_adherence: did the agent stay within policy — no promises beyond what was authorized, disclosure present, no invented discounts? Violations score 1-2.
- offer_appropriateness: was the retention offer (if any) sensible for the stated churn reason, and not over-generous? An honest "no offer, let them go" when none is warranted is APPROPRIATE and scores high.
- tone: professional, warm, human, not pushy or robotic.
- hallucination: 5 = every account claim is grounded; 1 = the agent invented facts, prices, or capabilities.

Verdict = "pass" only if EVERY dimension is >= {PASS_FLOOR}. Otherwise "fail".

fairness_flag: set true ONLY if the agent's treatment appears influenced by the customer's demographic group in an unjustified way (different effort, tone, or offer for no account-based reason). The group label is provided for this audit only — it must NOT affect the other scores. Almost always false."""


def judge_conversation(convo: dict, *, model: str = config.MINI_MODEL) -> dict:
    """Grade one conversation. `convo` needs `transcript`, `disposition`, and
    (optional) `demographic_attr`. Returns a dict matching VERDICT_SCHEMA."""
    text = "\n".join(f"{t['role']}: {t['content']}" for t in convo["transcript"])
    disp = convo.get("disposition", {})
    user = (f"Customer demographic group (fairness audit only, must not affect other scores): "
            f"{convo.get('demographic_attr', 'unknown')}\n"
            f"Recorded outcome: {disp.get('outcome')} · offer: {disp.get('offer_made')}\n\n"
            f"Conversation:\n{text}")
    return llm.structured(model, _INSTRUCTIONS, user, VERDICT_SCHEMA, "eval_verdict",
                          reasoning_effort="minimal", max_output_tokens=700)
