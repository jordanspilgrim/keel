"""Shared OpenAI client + Responses-API helpers.

Loads .env once (so OPENAI_API_KEY is available), exposes a singleton client,
and provides a `structured()` helper that returns a schema-validated dict via
Structured Outputs (plan §7 showcase). Keeping this in one place means every
service uses the same client and the same JSON-schema plumbing.
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

# Explicit path: find_dotenv() walks stack frames and fails under some runners.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from openai import OpenAI  # noqa: E402  (import after load_dotenv by design)

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def structured(model: str, instructions: str, user_input, schema: dict, name: str,
               *, reasoning_effort: str | None = None, max_output_tokens: int = 700) -> dict:
    """One-shot call returning a dict validated against `schema` (strict)."""
    kwargs = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        "max_output_tokens": max_output_tokens,
    }
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    resp = client().responses.create(**kwargs)
    text = (resp.output_text or "").strip()
    if not text:
        # Reasoning models can spend the whole token budget on reasoning and
        # return no text (status 'incomplete'). Surface that, don't blind-parse.
        detail = getattr(resp, "incomplete_details", None) or getattr(resp, "status", "unknown")
        raise RuntimeError(f"structured() got empty output (status={detail}); raise max_output_tokens "
                           f"or lower reasoning effort for model {model}.")
    return json.loads(text)
