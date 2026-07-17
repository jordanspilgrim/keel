"""Input & output guardrails — defense-in-depth (plan §4).

Independent, fail-closed checks the model cannot talk past. Input guardrails run
before the model acts; output guardrails run before the reply is sent. The
action guardrail (the deterministic policy layer) lives in policy.py.

Phase 2 fills these in. Every trip writes a guardrail_events row.

Input:
  check_jailbreak(text)   -> moderation/classifier + known-pattern heuristics
  check_scope(text)       -> in-domain (retention/CX) classifier; bound if not
  redact_pii(text)        -> strip card/SSN/health/credentials BEFORE log+embed
Output:
  check_grounding(reply, tool_results) -> claims trace to real tool output
  check_promise(reply, authorized)     -> no commitment the policy layer denied
  check_tone(reply)                    -> moderation for harmful/off-brand
"""

from __future__ import annotations

_PHASE = "Phase 2"


def check_jailbreak(text: str) -> dict:
    raise NotImplementedError(f"guardrails.check_jailbreak — {_PHASE}")


def check_scope(text: str) -> dict:
    raise NotImplementedError(f"guardrails.check_scope — {_PHASE}")


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Return (redacted_text, [redacted_field_types]). Runs before any log/embed."""
    raise NotImplementedError(f"guardrails.redact_pii — {_PHASE}")


def check_grounding(reply: str, tool_results: list[dict]) -> dict:
    raise NotImplementedError(f"guardrails.check_grounding — {_PHASE}")


def check_promise(reply: str, authorized: list[str]) -> dict:
    raise NotImplementedError(f"guardrails.check_promise — {_PHASE}")


def check_tone(reply: str) -> dict:
    raise NotImplementedError(f"guardrails.check_tone — {_PHASE}")
