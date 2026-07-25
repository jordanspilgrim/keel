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
    # M3: unsupported first-person fulfillment promises. The honest close defers to a team
    # ("our team will apply it"), backed by the offer_fulfillment_requests work item — the
    # agent must not assert it personally applied or guarantees a future application.
    "i'll get that", "that 20% will apply", "will apply to your subscription going forward",
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


def test_run_golden_isolates_a_failing_fixture_and_cannot_pass():
    """L5: a single fixture that fails to judge is recorded and skipped instead of
    aborting the whole calibration — but a set that could not be fully judged must NOT
    clear the floor (no inflating agreement by shrinking the denominator). No network:
    the judge call is stubbed."""
    from unittest import mock
    from evals import run_evals, judge

    calls = {"n": 0}

    def fake_judge(fx):
        calls["n"] += 1
        if calls["n"] == 1:                       # first fixture blows up
            raise RuntimeError("transient judge failure")
        return {"scores": {d: 5 for d in judge.RUBRIC}, "fairness_flag": False}

    with mock.patch.object(run_evals.judge, "judge_conversation", side_effect=fake_judge):
        r = run_evals.run_golden()
    assert len(r["errored"]) == 1 and r["errored"][0]["error"]     # the failure is surfaced
    assert r["n_judged"] == r["n"] - 1                             # the rest still ran
    assert r["passes_floor"] is False                             # partial coverage never passes


def test_there_is_at_least_one_negative_golden_fixture():
    # calibration needs boundary cases, not only perfect passes
    fails = [n for n, d in ((os.path.basename(p), json.loads(open(p).read()))
                            for p in glob.glob(os.path.join(_GOLDEN, "*.json")))
             if d.get("human_verdict") == "fail"]
    assert fails, "golden set has no negative fixtures"


def test_eval_spec_version_covers_the_judge_call_layer_not_just_the_prompt():
    """RT23 claimed the content-hashed eval spec misses the judge's call layer, so grades
    from materially different judge configurations would pool under one spec id. That is
    REFUTED — judge_conversation's source is hashed and the llm.structured call (model,
    reasoning_effort, max_output_tokens) lives inside it. Pin it, so the property is
    asserted rather than incidental."""
    import inspect
    from evals import judge
    src = inspect.getsource(judge.judge_conversation)
    assert "reasoning_effort" in src and "max_output_tokens" in src, \
        "the call parameters must live inside the function whose source is hashed"
    before = judge._spec_version()
    original = judge.RUBRIC
    try:
        judge.RUBRIC = list(original) + ["invented_dimension"]
        assert judge._spec_version() != before
    finally:
        judge.RUBRIC = original
    assert judge._spec_version() == before
