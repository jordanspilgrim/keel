"""Shared OpenAI client + Responses-API helpers.

Loads .env once (so OPENAI_API_KEY is available), exposes a singleton client,
and provides a `structured()` helper that returns a schema-validated dict via
Structured Outputs (plan §7 showcase). Keeping this in one place means every
service uses the same client and the same JSON-schema plumbing.
"""

from __future__ import annotations

import json
import os
import time

from dotenv import load_dotenv

# Explicit path: find_dotenv() walks stack frames and fails under some runners.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from openai import OpenAI  # noqa: E402  (import after load_dotenv by design)

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        # A per-request timeout so a stalled call fails and surfaces rather than
        # hanging a live turn indefinitely (the SDK also retries transient errors).
        _client = OpenAI(timeout=90.0)
    return _client


def structured(model: str, instructions: str, user_input, schema: dict, name: str,
               *, reasoning_effort: str | None = None, max_output_tokens: int = 700,
               retries: int = 2) -> dict:
    """One-shot call returning a dict validated against `schema` (strict).

    Retries on transient failures (empty output, parse error, network) so that
    batch grading is complete — "grade 100%" can't tolerate a silently dropped
    conversation. `retries` is the number of RE-tries (total attempts = 1 + n)."""
    kwargs = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        "max_output_tokens": max_output_tokens,
    }
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    last = None
    for attempt in range(retries + 1):
        try:
            resp = client().responses.create(**kwargs)
            text = (resp.output_text or "").strip()
            if not text:
                # Reasoning models can spend the whole budget on reasoning and
                # return no text (status 'incomplete'); bump the cap and retry.
                kwargs["max_output_tokens"] = int(kwargs["max_output_tokens"] * 1.6)
                detail = getattr(resp, "incomplete_details", None) or getattr(resp, "status", "unknown")
                raise RuntimeError(f"empty output (status={detail})")
            return json.loads(text)
        except Exception as e:  # transient: empty output, JSON parse, rate limit, network
            last = e
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"structured() failed after {retries + 1} attempts for model {model}: {last}")
