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
_STREET = (r"\b\d{1,6}\s+(?:[A-Z][a-z]+\.?\s+){1,3}"
           r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Terrace|Ter|Place|Pl)\b")
_PII_PATTERNS = [
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[REDACTED_CARD]", "card"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]", "ssn"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED_EMAIL]", "email"),
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), "[REDACTED_DOB]", "dob"),
    (re.compile(r"\b(?:\+?1[ -.]?)?\(?\d{3}\)?[ -.]?\d{3}[ -.]?\d{4}\b"), "[REDACTED_PHONE]", "phone"),
    (re.compile(_STREET), "[REDACTED_ADDRESS]", "address"),
    # Self-identified name ("my name is Jane Doe", "I'm John Smith") — heuristic, so it
    # only fires on the explicit-introduction shape, not any capitalized words. The
    # intro is kept; only the name is redacted.
    (re.compile(r"\b(?i:(my name is|i am|i'm|this is))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b"),
     r"\1 [REDACTED_NAME]", "name"),
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
    r"forget (?:everything|all|any|your|the|previous|earlier|prior|every )",
    r"developer mode|dev mode|no restrictions|without restrictions|unrestricted",
    r"pretend (?:the|that|you)|act as if|you are now",
    r"^\s*system\s*:|new directive|override (?:the )?(?:policy|rules|system|limits?)",
    r"replacement (?:policy|rules|instructions|directive)|obey the following|new (?:policy|rules?) (?:is|:)",
    r"from now on,? (?:you|ignore|forget|grant|approve)|as (?:an? )?(?:admin|administrator|developer)",
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
# NOTE: since the response contract is now the PRIMARY output control (the model
# declares its commitments structurally and we reconcile them against the offer
# ledger), these regexes are a SUPPLEMENTAL cross-check on display_text — catching
# prose that overstates the declared commitment. They cover spelled-out numbers and
# future/alternate completion phrasings, but are not expected to be exhaustive.
_SPELLED_NUM = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_DISCOUNT_CTX = re.compile(
    r"\b\d{1,3}\s*%\s*(?:off|discount)|discount of\b|"
    rf"\b(?:{'|'.join(_SPELLED_MAP)})\b[\s-]*percent|"
    rf"\b({_SPELLED_NUM})\b[\s-]*percent", re.IGNORECASE)
_PAUSE_RX = re.compile(
    rf"(\d+|{_SPELLED_NUM})[\s-]*month[\s-]*pause|"
    rf"pause (?:your (?:plan|subscription) )?(?:for |of )?(?:a |an )?(\d+|{_SPELLED_NUM})[\s-]*month",
    re.IGNORECASE)
# The action tools only PROPOSE an offer — they do not mutate a backend. So a reply
# claiming an offer is ALREADY applied / active / set up is an unsupported completion
# claim. We flag past/perfect/active-STATE language ("I've applied", "is now active"),
# NOT future commitments ("I'll set up a pause") or processing a requested
# cancellation ("I'll process your cancellation") — those are legitimate.
_COMPLETION_RX = re.compile(
    r"\b(i(?:'ve| have) (?:applied|activated|set up|processed|paused)(?: your| the| a)?\s"
    r"(?:discount|pause|offer|credit|plan)?|"
    r"(?:your |the )?(?:discount|pause|offer|credit) (?:has been|is|is now|are) "
    r"(?:applied|activated|active|processed|set up)|"
    r"(?:has|have) been (?:applied|activated|processed|set up))\b", re.IGNORECASE)
_MONTHS_WORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_MONEY_RX = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")

# --- robust extraction of the QUANTITIES a reply commits to ----------------
# The response contract's display_text must not express a bigger discount / longer
# pause than the structured commitment. Because prose can say a number many ways
# ("15%", "15 percent", "twenty-five percent", "a quarter off"), we extract EVERY
# discount percentage and pause length from the prose and reconcile each against
# the committed terms — rather than trying to recognize one canonical phrasing.
_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_FRACTION_PCT = {"a third": 33, "one third": 33, "a quarter": 25, "one quarter": 25,
                 "a half": 50, "one half": 50, "half": 50, "three quarters": 75,
                 "two thirds": 66}
_SPELLED_WORD_RX = re.compile(r"\b((?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
                              r"(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?|"
                              r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                              r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
                              r"one hundred|hundred)\b", re.IGNORECASE)


def _word_to_num(phrase: str) -> int | None:
    """Convert a spelled cardinal 1-100 (incl. 'twenty-five') to an int, else None."""
    p = phrase.lower().strip()
    if p in ("hundred", "one hundred"):
        return 100
    if p in _ONES:
        return _ONES[p]
    parts = re.split(r"[\s-]+", p)
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES and _ONES[parts[1]] < 10:
        return _TENS[parts[0]] + _ONES[parts[1]]
    if p in _TENS:
        return _TENS[p]
    return None


def extract_discount_pcts(text: str) -> list[float]:
    """Every discount percentage the prose expresses (digits, 'percent', spelled,
    or a fraction 'off') — used to reconcile prose against the committed offer."""
    out: list[float] = []
    for m in re.finditer(r"(\d{1,3})\s*(?:%|percent\b)", text, re.IGNORECASE):
        out.append(float(m.group(1)))
    # spelled '<number> percent'
    for m in re.finditer(r"\b([a-z][a-z\s-]*?)\s+percent\b", text, re.IGNORECASE):
        v = _word_to_num(m.group(1).split()[-1] if " " in m.group(1) else m.group(1))
        # handle 'twenty-five percent' where the whole token is the number
        v2 = _word_to_num(m.group(1).strip())
        for cand in (v2, v):
            if cand is not None:
                out.append(float(cand))
                break
    # fraction 'off' / 'discount' (a quarter off, half off)
    low = text.lower()
    for phrase, val in _FRACTION_PCT.items():
        if re.search(rf"\b{re.escape(phrase)}\b[^.]*\b(off|discount)\b", low) or \
           re.search(rf"\b(off|discount)\b[^.]*\b{re.escape(phrase)}\b", low):
            out.append(float(val))
    return out


def extract_pause_months(text: str) -> list[int]:
    """Every pause length (in months) the prose expresses."""
    out: list[int] = []
    for m in re.finditer(r"(\d{1,3}|[a-z][a-z\s-]*?)[\s-]*month", text, re.IGNORECASE):
        raw = m.group(1).strip()
        if raw.isdigit():
            out.append(int(raw))
        else:
            v = _word_to_num(raw.split()[-1]) if raw else None
            if v is not None:
                out.append(v)
    return out


def check_tone(reply: str) -> dict:
    """Moderation pass (free). Flags harmful/abusive content in the reply. If the
    moderation service is unavailable, returns an EXPLICIT `degraded` result (not a
    silent fail-open) so the finalizer can bound or route it."""
    try:
        r = llm.client().moderations.create(model="omni-moderation-latest", input=reply)
        flagged = bool(r.results[0].flagged)
        return {"flagged": flagged, "degraded": False, "reason": "moderation flagged" if flagged else ""}
    except Exception:
        return {"flagged": False, "degraded": True, "reason": "moderation unavailable"}


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
        raw = next(g for g in pm.groups() if g)
        months = int(raw) if str(raw).isdigit() else _MONTHS_WORD.get(str(raw).lower(), 1)
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


def check_completion_claim(reply: str) -> bool:
    """True if the reply claims an offer/action is already applied/active — the
    tools only PROPOSE, so a completion claim is unsupported."""
    return bool(_COMPLETION_RX.search(reply))


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
