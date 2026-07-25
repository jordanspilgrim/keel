"""Repo-wide test guards.

The offline suite advertises "no network". That was enforced only by each test module
remembering to stub every LLM entry point — and a new call site (the M4 injection classifier
wired into the shared `screen_input` path) silently leaked a real, billed API call into one
module whose fixture hadn't been updated. The tests still passed, because the classifier
swallows exceptions, so nothing surfaced the violation.

This makes the invariant STRUCTURAL: any outbound socket connect during the offline suite
fails loudly with the offending address. Tests that legitimately need a socket (the real
two-thread race test uses loopback) are unaffected — only non-local addresses are blocked.

The tripwire raises a BaseException subclass ON PURPOSE. Production code is full of
deliberate fail-safe handlers (`except Exception: return "continue"` in the decision
classifier, `except Exception: pass` around telemetry), and every one of them would
CATCH a tripwire that derived from Exception — turning a leaked, billed network call
back into a silent pass, which is exactly the failure this file exists to prevent.
BaseException passes straight through those handlers, as KeyboardInterrupt does.
"""

from __future__ import annotations

import socket

import pytest

_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


class OfflineSuiteNetworkCall(BaseException):
    """Deliberately NOT an Exception — see the module docstring."""


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _ALLOWED_HOSTS:
            raise OfflineSuiteNetworkCall(
                f"offline test suite attempted a NETWORK call to {host!r} — the suite must be "
                f"no-network. Stub the LLM entry point (e.g. guardrails.check_scope, "
                f"guardrails.classify_injection, llm.structured) in this module's fixture."
            )
        return real_connect(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)
