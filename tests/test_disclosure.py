"""Unit tests for the AI-disclosure guardrail (Art. 50). No API calls."""

from __future__ import annotations

import config
from agent import disclosure


def test_disclosure_message_is_the_configured_text():
    msg = disclosure.disclosure_message()
    assert msg["role"] == "assistant"
    assert msg["content"] == config.AI_DISCLOSURE


def test_has_disclosure_true_when_first_assistant_turn_is_disclosure():
    t = [disclosure.disclosure_message(), {"role": "user", "content": "cancel please"}]
    assert disclosure.has_disclosure(t)


def test_has_disclosure_false_when_missing():
    assert not disclosure.has_disclosure([{"role": "user", "content": "cancel please"}])
    # first assistant turn is NOT the disclosure
    assert not disclosure.has_disclosure([{"role": "assistant", "content": "Sure, cancelling now."}])
