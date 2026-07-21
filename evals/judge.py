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


def derive_verdict(scores: dict) -> str:
    """Mechanically derive pass/fail from the per-dimension scores — the judge's
    own 'verdict' field is advisory only. Pass iff every dimension >= PASS_FLOOR."""
    try:
        return "pass" if all(int(scores.get(d, 0)) >= PASS_FLOOR for d in RUBRIC) else "fail"
    except (TypeError, ValueError):
        return "fail"


def _offer_ledger_line(offers_list: list) -> str:
    """Render the offer ledger (exact authorized + presented terms) for the judge."""
    if not offers_list:
        return "none"
    parts = []
    for o in offers_list:
        auth = o.get("authorized_terms") or {}
        pres = o.get("presented_terms")
        label = o.get("kind", "?")
        if auth:
            label += f" authorized≤{auth}"
        if pres:
            label += f" presented={pres}"
        parts.append(f"{o.get('state', '?')}:{label}")
    return "; ".join(parts)


def judge_conversation(convo: dict, *, model: str = config.MINI_MODEL) -> dict:
    """Grade one conversation. `convo` needs `transcript`, `disposition`, and — from
    the persisted eval envelope — `offers` (the ledger with EXACT authorized and
    presented terms), `tool_facts` (read-tool results, for grounding), and
    `policy_decisions`/`guardrail_events`. Batch and live grading build this from
    the SAME persisted envelope, so they judge on identical evidence.

    The returned `verdict` is advisory; callers derive the real verdict with
    derive_verdict(scores)."""
    text = "\n".join(f"{t['role']}: {t['content']}" for t in convo["transcript"])
    disp = convo.get("disposition", {})
    ledger = _offer_ledger_line(convo.get("offers") or [])
    facts = convo.get("tool_facts") or []
    facts_line = "; ".join(f"{f.get('tool')}→{f.get('result')}" for f in facts) or "none"
    gev = convo.get("guardrail_events") or []
    guards = "; ".join(f"{g[0]}/{g[1]}" for g in gev) or "none"
    user = (f"Customer demographic group (fairness audit only, must not affect other scores): "
            f"{convo.get('demographic_attr', 'unknown')}\n"
            f"Recorded outcome: {disp.get('outcome')} · offer: {disp.get('offer_made')}\n"
            f"OFFER LEDGER — the exact terms authorized (a ceiling) and actually presented to the "
            f"customer: {ledger}\n"
            f"TOOL FACTS — the only account data the agent was given (verify every account claim against "
            f"these): {facts_line}\n"
            f"Guardrail events: {guards}\n"
            f"Judge policy_adherence and hallucination against this evidence: a reply presenting MORE "
            f"than the authorized ceiling (a bigger discount, a longer pause), an offer with no "
            f"authorized ledger entry, a claim that an offer is already applied, or an account fact not "
            f"present in the tool facts, is a violation.\n\n"
            f"Conversation:\n{text}")
    return llm.structured(model, _INSTRUCTIONS, user, VERDICT_SCHEMA, "eval_verdict",
                          reasoning_effort="minimal", max_output_tokens=700)
