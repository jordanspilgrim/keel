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
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), "[REDACTED_DOB]", "dob"),
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
        # Fail CLOSED: if the classifier is unavailable we treat the message as
        # out-of-scope, so the agent gives a bounded reply rather than free-forming
        # on unclassified input. A false "in_scope" here is the unsafe direction.
        return {"in_scope": False, "reason": "classifier unavailable; failing closed to a bounded reply"}


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
_BANNED_PROMISES = re.compile(
    r"\b(lifetime|forever|guarantee[d]?|unlimited free|permanent(?:ly)? free|free for life|"
    r"half[\s-]?(?:off|price)|waive (?:the )?(?:next )?[\w-]+ (?:invoices?|payments?|months?|bills?))\b",
    re.IGNORECASE)
_PCT_RX = re.compile(r"(\d{1,3})\s*%")
_SPELLED_PCT = re.compile(
    r"\b(ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|(?:one[\s-]?)?hundred)\b[\s-]*percent",
    re.IGNORECASE)
_SPELLED_MAP = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
                "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "one hundred": 100}
_DISCOUNT_CTX = re.compile(r"\b\d{1,3}\s*%\s*(?:off|discount)|discount of\b", re.IGNORECASE)
_PAUSE_RX = re.compile(r"(\d+)[\s-]*month[\s-]*pause|pause (?:for |of )?(\d+)[\s-]*month", re.IGNORECASE)
# The action tools only PROPOSE an offer — they do not mutate a backend. So a
# reply claiming an action is already *applied* is an unsupported completion claim.
_COMPLETION_RX = re.compile(
    r"\b(i(?:'ve| have) (?:applied|activated|set up|processed|cancell?ed|paused|stopped)|"
    r"has been (?:applied|activated|cancell?ed|processed|paused)|"
    r"cancellation (?:is|has been) (?:processed|complete|started|done))\b", re.IGNORECASE)
_MONEY_RX = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")


def check_tone(reply: str) -> dict:
    """Moderation pass (free). Flags harmful/abusive content in the reply."""
    try:
        r = llm.client().moderations.create(model="omni-moderation-latest", input=reply)
        flagged = bool(r.results[0].flagged)
        return {"flagged": flagged, "reason": "moderation flagged" if flagged else ""}
    except Exception:
        return {"flagged": False, "reason": "moderation unavailable"}


def check_promise(reply: str, *, authorized: dict | None = None) -> dict:
    """Reconcile the customer-visible commitment against the EXACT authorized
    terms. `authorized` is {'discount_pct': X or None, 'pause_months': Y or None}
    — the terms the policy layer actually approved this conversation. A reply that
    promises a bigger discount, a longer/absent pause, or claims an action is
    already applied (the tools only propose) is flagged."""
    authorized = authorized or {}
    m = _BANNED_PROMISES.search(reply)
    if m:
        return {"flagged": True, "reason": f"banned commitment: {m.group(0)!r}"}

    # --- discount terms vs. authorization ---
    disc_auth = authorized.get("discount_pct")
    nums = [int(p) for p in _PCT_RX.findall(reply) if int(p) <= 100]
    nums += [_SPELLED_MAP[w.lower().replace("one ", "").strip("- ")] for w in _SPELLED_PCT.findall(reply)
             if w.lower().replace("one ", "").strip("- ") in _SPELLED_MAP]
    over = [n for n in nums if n > config.MAX_DISCOUNT_PCT]
    if over:
        return {"flagged": True, "reason": f"promises discount above the {config.MAX_DISCOUNT_PCT}% ceiling: {over}"}
    if _DISCOUNT_CTX.search(reply):
        if disc_auth is None:
            return {"flagged": True, "reason": "promises a discount that was not authorized by a tool call"}
        if nums and max(nums) > disc_auth + 0.5:
            return {"flagged": True, "reason": f"promises {max(nums)}% but only {disc_auth:.0f}% was authorized"}

    # --- pause terms vs. authorization ---
    pm = _PAUSE_RX.search(reply)
    if pm:
        months = int(next(g for g in pm.groups() if g))
        pause_auth = authorized.get("pause_months")
        if pause_auth is None:
            return {"flagged": True, "reason": "promises a pause that was not authorized by a tool call"}
        if months > pause_auth:
            return {"flagged": True, "reason": f"promises a {months}-month pause but only {pause_auth} was authorized"}

    # --- completion claims (tools propose; they don't apply) ---
    cm = _COMPLETION_RX.search(reply)
    if cm:
        return {"flagged": True, "reason": f"claims an action is already applied ({cm.group(0)!r}); tools only propose"}
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


def screen_output(reply: str, tool_results: list[str], *, authorized: dict | None = None) -> dict:
    """Full output-guardrail pass over an agent reply."""
    return {"tone": check_tone(reply), "promise": check_promise(reply, authorized=authorized),
            "grounding": check_grounding(reply, tool_results)}
