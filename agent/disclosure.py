"""AI-disclosure turn — EU AI Act Art. 50 (binding 2 Aug 2026), plan §5.

Enforced as an input-side guardrail: the disclosure is prepended to every
conversation and its presence is *checked*, so it can never be skipped. Fully
implemented (no API) because it's foundational to Phases 1–2.
"""

from __future__ import annotations

import config


def disclosure_message() -> dict:
    """The mandatory opening assistant turn stating the user is talking to AI."""
    return {"role": "assistant", "content": config.AI_DISCLOSURE}


def has_disclosure(transcript: list[dict]) -> bool:
    """True iff the first assistant turn is the disclosure (compliance check)."""
    for turn in transcript:
        if turn.get("role") == "assistant":
            return turn.get("content", "").startswith(config.AI_DISCLOSURE[:40])
    return False
