"""H3 precondition: every POSITIVE golden fixture must satisfy the current output
invariants. The golden set is the evidence that self-grading is meaningful, so a 'pass'
fixture must not encode behavior the production runtime was hardened against — no
completed-action claims, and no data-retention / billing-period-access promises the
server-authored templates never make. No network.
"""

from __future__ import annotations

import glob
import json
import os

from agent import guardrails

_GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals", "golden")

# Phrases the current server output never emits (obsolete free-form claims).
_FORBIDDEN = [
    "data and settings", "everything will be right where you left",
    "won't be charged again", "keep access through", "retain access",
    "i've applied", "i've set up", "i've processed",
]


def _pass_fixtures():
    for path in sorted(glob.glob(os.path.join(_GOLDEN, "*.json"))):
        d = json.loads(open(path).read())
        if d.get("human_verdict") == "pass":
            yield os.path.basename(path), d


def test_positive_golden_fixtures_make_no_completed_action_claim():
    offenders = []
    for name, d in _pass_fixtures():
        for t in d["transcript"]:
            if t["role"] == "assistant" and guardrails.check_completion_claim(t["content"]):
                offenders.append((name, t["content"][:70]))
    assert not offenders, f"pass fixtures claim a completed action: {offenders}"


def test_positive_golden_fixtures_have_no_obsolete_unsupported_claims():
    offenders = []
    for name, d in _pass_fixtures():
        for t in d["transcript"]:
            if t["role"] != "assistant":
                continue
            low = t["content"].lower()
            for phrase in _FORBIDDEN:
                if phrase in low:
                    offenders.append((name, phrase))
    assert not offenders, f"pass fixtures contain claims the runtime forbids: {offenders}"


def test_there_is_at_least_one_negative_golden_fixture():
    # calibration needs boundary cases, not only perfect passes
    fails = [n for n, d in ((os.path.basename(p), json.loads(open(p).read()))
                            for p in glob.glob(os.path.join(_GOLDEN, "*.json")))
             if d.get("human_verdict") == "fail"]
    assert fails, "golden set has no negative fixtures"
