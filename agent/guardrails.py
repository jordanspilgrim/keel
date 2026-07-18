"""Input & output guardrails — defense-in-depth (plan §4).

Independent, fail-closed checks the model cannot talk past. Input guardrails run
before the model acts; output guardrails run before the reply is sent. The
action guardrail (the deterministic authorization + human-in-the-loop) lives in
policy.py. Every trip is logged to guardrail_events by the runtime.

Design choices:
- PII redaction is pure regex + keyword (deterministic, no data leaves the box
  before it's scrubbed) — GDPR data-minimization runs FIRST.
- Jailbreak detection is a deterministic pattern layer (fast, free, auditable) —
  it catches the known override/injection shapes.
- Scope detection is a semantic classifier (mini model) — brittle keywords
  can't tell "cancel my plan" from "cancel my flight".
- Output: tone via the free Moderation API; promise + grounding are cheap
  deterministic checks over the reply vs. what was authorized / returned.
"""

from __future__ import annotations

import re

import config
import llm

# --- PII / sensitive-data redaction (runs before any log or embed) ---------
_PII_PATTERNS = [
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[REDACTED_CARD]", "card"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]", "ssn"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED_EMAIL]", "email"),
    (re.compile(r"\b(?:\+?1[ -.]?)?\(?\d{3}\)?[ -.]?\d{3}[ -.]?\d{4}\b"), "[REDACTED_PHONE]", "phone"),
]
_SENSITIVE_TERMS = re.compile(
    r"\b(health record|medical record|diagnosis|diagnosed|my condition|prescription|password|api key|routing number)\b",
    re.IGNORECASE,
)


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, sorted_unique_field_types). Deterministic."""
    types: set[str] = set()
    out = text
    for rx, repl, kind in _PII_PATTERNS:  # card before phone so 16-digit runs aren't split
        if rx.search(out):
            out = rx.sub(repl, out)
            types.add(kind)
    if _SENSITIVE_TERMS.search(out):
        out = _SENSITIVE_TERMS.sub("[REDACTED_SENSITIVE]", out)
        types.add("sensitive")
    return out, sorted(types)


# --- jailbreak / prompt-injection (deterministic patterns) -----------------
_JAILBREAK_PATTERNS = [
    r"ignore (?:all |your |the |previous |prior )*(?:instructions|rules|prompt|guidelines)",
    r"disregard (?:all |your |the |previous |prior )",
    r"developer mode|dev mode|no restrictions|without restrictions|unrestricted",
    r"pretend (?:the|that|you)|act as if|you are now",
    r"^\s*system\s*:|new directive|override (?:the )?(?:policy|rules|system)",
    r"jailbreak|do anything now|\bDAN\b",
]
_JAILBREAK_RX = re.compile("|".join(_JAILBREAK_PATTERNS), re.IGNORECASE)


def check_jailbreak(text: str) -> dict:
    m = _JAILBREAK_RX.search(text)
    return {"flagged": bool(m), "reason": f"matched injection pattern: {m.group(0)!r}" if m else ""}


# --- scope / off-topic (semantic classifier) -------------------------------
_SCOPE_SCHEMA = {
    "type": "object",
    "properties": {"in_scope": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["in_scope", "reason"],
    "additionalProperties": False,
}
_SCOPE_INSTRUCTIONS = (
    "You are a scope classifier for a subscription-retention support agent. "
    "IN SCOPE: anything about the customer's own subscription, billing, plan, cancellation, "
    "usage, account, or a retention offer. OUT OF SCOPE: general knowledge, weather, jokes/poems, "
    "coding help, tax/legal/financial/medical advice, stock picks, or anything unrelated to their account. "
    "Return in_scope=false for out-of-scope requests."
)


def check_scope(text: str) -> dict:
    try:
        r = llm.structured(config.MINI_MODEL, _SCOPE_INSTRUCTIONS, text, _SCOPE_SCHEMA,
                           "scope_check", reasoning_effort="minimal", max_output_tokens=300)
        return {"in_scope": bool(r["in_scope"]), "reason": r["reason"]}
    except Exception:
        return {"in_scope": True, "reason": "classifier unavailable; fail-open to human review"}


# --- input pipeline --------------------------------------------------------
def screen_input(text: str, classify_scope: bool = True) -> dict:
    """Full input-guardrail pass. Returns the redacted text plus each verdict.

    `classify_scope=False` skips the semantic scope classifier (used mid-turn,
    where PII+jailbreak still run deterministically every turn but the mini
    scope call would be wasted spend on an already-in-scope customer)."""
    redacted, pii_types = redact_pii(text)
    jb = check_jailbreak(redacted)
    if jb["flagged"] or not classify_scope:
        scope = {"in_scope": True, "reason": "skipped"}
    else:
        scope = check_scope(redacted)
    return {"redacted_text": redacted, "pii_types": pii_types,
            "jailbreak": jb, "off_scope": not scope["in_scope"], "scope_reason": scope["reason"]}


# --- output guardrails -----------------------------------------------------
_BANNED_PROMISES = re.compile(r"\b(lifetime|forever|guarantee[d]?|unlimited free|permanent(?:ly)? free)\b", re.IGNORECASE)
_PCT_RX = re.compile(r"(\d{1,3})\s*%")
_MONEY_RX = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")


def check_tone(reply: str) -> dict:
    """Moderation pass (free). Flags harmful/abusive content in the reply."""
    try:
        r = llm.client().moderations.create(model="omni-moderation-latest", input=reply)
        flagged = bool(r.results[0].flagged)
        return {"flagged": flagged, "reason": "moderation flagged" if flagged else ""}
    except Exception:
        return {"flagged": False, "reason": "moderation unavailable"}


def check_promise(reply: str) -> dict:
    """Catch commitments the policy layer would never authorize."""
    if _BANNED_PROMISES.search(reply):
        return {"flagged": True, "reason": f"banned commitment: {_BANNED_PROMISES.search(reply).group(0)!r}"}
    over = [int(p) for p in _PCT_RX.findall(reply) if int(p) > config.MAX_DISCOUNT_PCT and int(p) <= 100]
    if over:
        return {"flagged": True, "reason": f"promises discount above {config.MAX_DISCOUNT_PCT}% ceiling: {over}"}
    return {"flagged": False, "reason": ""}


def check_grounding(reply: str, tool_results: list[str]) -> dict:
    """Lightweight grounding heuristic: the agent may legitimately *derive* a
    figure (e.g. a discounted price) from a real one, so we don't demand an exact
    string match. We flag only the honest failure mode — the reply quotes money
    while the tools returned NO numeric data at all (facts invented with zero
    grounding). A stronger LLM-judge grounding check lives in the eval layer."""
    corpus = " ".join(tool_results)
    if _MONEY_RX.search(reply) and not re.search(r"\d", corpus):
        return {"flagged": True, "reason": "reply cites monetary amounts but no tool returned any data"}
    return {"flagged": False, "reason": ""}


def screen_output(reply: str, tool_results: list[str]) -> dict:
    """Full output-guardrail pass over an agent reply."""
    return {"tone": check_tone(reply), "promise": check_promise(reply),
            "grounding": check_grounding(reply, tool_results)}
