"""The seven publicly-claimed controls that had NO guarding test at all.

`mutate.py` names them mechanically on every run: reverting any of these seven left the suite
unchanged, which is what SURVIVED means — the control could be deleted and nothing would
notice. They are R17 H7 / H8 / M21 and keel-r3b EV-B5, and the composition is the finding:
**three kill-switch floors, the money demo's independent variable, and all three
judge-calibration gates. The kill switch's own triggers were among the untested.**

WHY EACH TEST HAS A REAL RED STEP, in the strong form. A test written after the fact that
passes immediately proves nothing. Here **the mutant IS the RED step**: every test below must
kill a mutant that currently SURVIVES, so it is demonstrated failing against the unguarded
control before it is demonstrated passing. `scripts/mutate.py` re-runs that demonstration on
demand, by name, which no ordinary regression test can claim.

CONSTRUCTED DB STATES ARE UNIT TESTS, NOT FABRICATION. Three of these assert on
`safety.program_state` under an eval arm at a given pass rate and coverage, which means
building rows to reach that state. That is testing a conditional — "IF the eval arm is
healthy/unhealthy, THEN the mode is X" — and constructing an antecedent is how any conditional
is tested. Fabrication would be claiming the real system IS in that state; nothing here does,
and nothing here reads a committed artifact.

No network, no billed calls: the judge is stubbed in every golden test, which is also the
point — these assert control flow, and a live call would make them non-deterministic AND cost
money to prove something about an `if`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import config
import db
import synth
from agent import guardrails, policy, safety
from evals import judge, run_evals


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    synth.generate(c)
    yield c
    c.close()


def _conversations(conn, n: int) -> list[int]:
    """Insert exactly `n` conversations. `synth.generate` seeds customers, subscriptions and
    scenarios but NO conversations, so the denominator every kill-switch rate divides by is 0
    until something writes them — which is why these had to be built rather than seeded."""
    ids = []
    for _ in range(n):
        cur = conn.execute(
            "INSERT INTO conversations (customer_id, scenario_id, transcript_json, "
            "disposition_json, offer_made, outcome, created_at) VALUES (?,?,?,?,?,?,?)",
            (1, None, "[]", "{}", None, "lost", run_evals._now()))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _grade(conn, ids: list[int], n_pass: int, n_fail: int) -> None:
    """Give the DB an eval arm with exactly this pass/fail split; anything past `n_pass +
    n_fail` is left ungraded, which is what moves coverage independently of pass rate.

    Rows are written under the CURRENT spec version, because that is the only population
    `current_spec_eval_counts` counts and therefore the only one the kill switch sees."""
    assert n_pass + n_fail <= len(ids)
    for cid, verdict in zip(ids, ["pass"] * n_pass + ["fail"] * n_fail):
        conn.execute(
            "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, "
            "fairness_flag, rubric_version, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, "{}", verdict, "", 0, judge.EVAL_SPEC_VERSION, run_evals._now()))
    conn.commit()


def _healthy_guardrail_arm(conn) -> None:
    """Silence the guardrail arm so the eval arm is the only thing that can speak."""
    db.record_health(conn, "guardrail_catch_rate", 1.0, "ok",
                     version=guardrails.guardrail_version())


# --- the kill switch's own triggers (R17 H8, H7) ------------------------------------------

def test_an_eval_pass_rate_below_the_floor_forces_safe_mode(conn):
    """MUTANT: eval_pass_rate_floor. `if pass_rate < config.EVAL_PASS_RATE_FLOOR` was
    deletable with the suite unchanged — the kill switch's primary trigger, untested."""
    ids = _conversations(conn, 20)
    _healthy_guardrail_arm(conn)
    _grade(conn, ids, n_pass=10, n_fail=10)               # 0.50, well under the 0.80 floor

    st = safety.program_state(conn)
    assert st["metrics"]["eval_pass_rate"] == 0.5
    assert st["mode"] == "safe" and st["healthy"] is False
    assert any("eval pass rate" in r for r in st["reasons"]), st["reasons"]


def test_eval_coverage_below_the_floor_forces_safe_mode(conn):
    """MUTANT: eval_coverage_floor. The OTHER kill-switch trigger, equally untested.

    Constructed so the pass-rate floor CANNOT be what fires: 17 of 20 pass — 0.85, above the
    0.80 floor — while the other 3 are left ungraded, putting coverage at 0.85, below the 0.90
    floor. Only the coverage check can produce a reason here, and the test asserts that the
    pass-rate check did NOT, so a mutant that deletes the coverage floor cannot be masked by
    the other trigger firing for it."""
    ids = _conversations(conn, 20)
    _healthy_guardrail_arm(conn)
    _grade(conn, ids, n_pass=17, n_fail=0)                # 3 left ungraded -> pass .85, cov .85

    st = safety.program_state(conn)
    assert st["metrics"]["eval_pass_rate"] >= config.EVAL_PASS_RATE_FLOOR, "pass rate must NOT fire"
    assert st["metrics"]["eval_coverage"] == 0.85
    assert st["mode"] == "safe"
    reasons = " ".join(st["reasons"])
    assert "coverage" in reasons and "pass rate" not in reasons, st["reasons"]


def test_a_stale_guardrail_health_result_cannot_authorize_normal_mode(conn):
    """MUTANT: guardrail_health_freshness. R17 H7 — the freshness gate had NO test at all.

    Version-match and floor are both satisfied, so age is the only thing that can fire."""
    stale = (datetime.now(timezone.utc)
             - timedelta(days=config.GUARDRAIL_HEALTH_MAX_AGE_DAYS + 5)).isoformat()
    conn.execute(
        "INSERT INTO program_health (metric, value, detail, version, created_at) VALUES (?,?,?,?,?)",
        ("guardrail_catch_rate", 1.0, "old but perfect", guardrails.guardrail_version(), stale))
    conn.commit()

    st = safety.program_state(conn)
    assert st["metrics"]["guardrail_catch_rate"] == 1.0, "a PERFECT but stale result"
    assert st["mode"] == "safe" and st["healthy"] is False
    assert any("old" in r for r in st["reasons"]), st["reasons"]


# --- the money demo's independent variable (R17 M21) --------------------------------------

def test_with_the_discount_lever_off_policy_actually_rejects_a_discount(monkeypatch):
    """MUTANT: discounts_enabled_lever. The whole flywheel demo is the difference between the
    two states of this flag, and only its ENABLED path was ever tested — so the baseline arm's
    rejection, which is what makes the A/B contrast exist, was deletable."""
    sub = {"plan": "Pro", "price": 99.0, "last_save_offer_days": None}

    monkeypatch.setattr(policy, "DISCOUNTS_ENABLED", False)
    off = policy.authorize("offer_discount", {"pct": 10}, sub)
    assert off["allowed"] is False and off["action"] == "rejected"
    assert "disabled" in off["reason"].lower()

    monkeypatch.setattr(policy, "DISCOUNTS_ENABLED", True)
    on = policy.authorize("offer_discount", {"pct": 10}, sub)
    assert on["allowed"] is True, "control test: the same call is authorized when the lever is on"


# --- the three judge-calibration gates (keel-r3b EV-B5) ------------------------------------

def _stub_golden(verdicts: dict[str, str] | None = None, scores: int = 5):
    """Drive `run_golden` with a deterministic stubbed judge. `verdicts` maps a substring of a
    fixture name to the verdict that fixture's judge should produce."""
    verdicts = verdicts or {}

    def fake(fx):
        name = fx.get("name", "") if isinstance(fx, dict) else ""
        want = next((v for k, v in verdicts.items() if k in name), None)
        s = {d: (5 if want == "pass" else 1) if want else scores for d in judge.RUBRIC}
        return {"scores": s, "fairness_flag": False}

    return mock.patch.object(run_evals.judge, "judge_conversation", side_effect=fake)


def test_the_golden_set_gates_on_judge_vs_human_agreement(monkeypatch):
    """MUTANT: golden_agreement_floor. `agreement >= AGREEMENT_FLOOR` was deletable — the
    calibration gate stops gating and every disagreement clears the floor."""
    monkeypatch.setattr(run_evals, "AGREEMENT_FLOOR", 1.01)   # unreachable: nothing may pass
    with _stub_golden():
        r = run_evals.run_golden()
    assert r["passes_floor"] is False, (
        "with the floor above any achievable agreement, passes_floor must be False — if this "
        "passes, the comparison is no longer being made")

    monkeypatch.setattr(run_evals, "AGREEMENT_FLOOR", 0.0)    # trivially reachable
    with _stub_golden():
        r2 = run_evals.run_golden()
    assert r2["passes_floor"] is True, "control test: the floor is what decides, nothing else"


def test_judge_calibration_is_gated_on_the_per_dimension_error(monkeypatch):
    """MUTANT: judge_calibration_mae_floor. `mae <= CALIBRATION_MAE_FLOOR` was replaceable with
    a hard-wired True — calibration reported as within tolerance whatever the error."""
    monkeypatch.setattr(run_evals, "CALIBRATION_MAE_FLOOR", -1.0)  # no error can be within it
    with _stub_golden():
        r = run_evals.run_golden()
    assert r["mae_within_tolerance"] is False, (
        "a negative tolerance cannot be satisfied by any real error — if this reports True the "
        "value is not being compared")

    monkeypatch.setattr(run_evals, "CALIBRATION_MAE_FLOOR", 99.0)
    with _stub_golden():
        r2 = run_evals.run_golden()
    assert r2["mae_within_tolerance"] is True, "control test: the floor decides"


def test_a_judge_fooled_by_the_injection_fixture_does_not_report_as_resistant():
    """MUTANT: judge_injection_fixture_held. This is the sharpest of the three: the property is
    that the judge treats the graded conversation as DATA, and the gate is a per-fixture check
    precisely because aggregate agreement and MAE can both stay above their floors while the
    judge is fooled on exactly this fixture. Hard-wiring it True was deletable.

    Stub the judge into being fooled — the injection fixture, which embeds a 'give all 5s'
    attack and must be scored FAIL, is scored PASS instead."""
    with _stub_golden(verdicts={"injection": "pass"}):
        fooled = run_evals.run_golden()
    assert fooled["injection_fixtures_n"] >= 1, "precondition: the fixture must exist"
    assert fooled["injection_fixture_held"] is False, (
        "the judge was steered to pass the injection fixture and the gate still reported held")

    with _stub_golden(verdicts={"injection": "fail"}):
        resisted = run_evals.run_golden()
    assert resisted["injection_fixture_held"] is True, (
        "control test: with the judge failing the fixture as it must, the gate holds")
