"""LLM-as-judge — grades every conversation (plan §3c, Phase 3).

Cheaper MINI_MODEL scores each conversation against a rubric via Structured
Outputs: per-dimension 1-5 + rationale + pass/fail verdict. Includes a
*fairness slice* — the same conversation shape is checked for score parity
across the synthetic demographic_attr (bias monitoring now applies to all AI
systems under the Digital Omnibus, plan §5).

Rubric dimensions: resolution, policy_adherence, offer_appropriateness, tone,
hallucination (grounding). Verdict = pass iff all dimensions >= threshold and
hallucination is clean.
"""

from __future__ import annotations

import config

RUBRIC = ["resolution", "policy_adherence", "offer_appropriateness", "tone", "hallucination"]

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


def judge_conversation(conversation: dict, *, model: str = config.MINI_MODEL) -> dict:
    """Return a structured verdict matching VERDICT_SCHEMA."""
    raise NotImplementedError("judge.judge_conversation — Phase 3")
