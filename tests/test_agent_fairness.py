"""M2 — the agent-treatment fairness harness. Offline: the agent is stubbed, so these test
the harness math (counterfactual pairs, gap + CI, minimum-sample gate), not the model."""

from __future__ import annotations

from evals import agent_fairness as af


def _base(n):
    return [{"customer_id": i, "account": {"plan": "Pro", "price": 99, "tenure": 12},
             "churn_reason": "Price too high", "opening_message": "I want to cancel, it's too expensive."}
            for i in range(n)]


def test_build_pairs_differ_only_in_the_observable_proxy():
    pairs = af.build_pairs(_base(5), n_pairs=5)
    assert len(pairs) == 5
    for p in pairs:
        a, b = p["members"]["group_a"], p["members"]["group_b"]
        assert a["account"] == b["account"] and a["churn_reason"] == b["churn_reason"]  # identical state
        assert a["opening_message"] != b["opening_message"]                              # only the name differs
        assert a["customer_id"] == b["customer_id"]


def test_report_detects_differential_treatment_with_a_ci():
    """A biased agent (group_b never offered) over a sufficient sample is flagged, and the
    offer-rate gap CI excludes 0."""
    pairs = af.build_pairs(_base(30), n_pairs=30)

    def biased(member):
        if member["group"] == "group_a":
            return {"offer_kind": "discount", "offer_terms": {"pct": 20}, "escalated": False, "outcome": "saved"}
        return {"offer_kind": None, "offer_terms": None, "escalated": True, "outcome": "lost"}

    rep = af.report(af.measure(pairs, biased))
    assert rep["sufficient_sample"] is True
    assert rep["offer_rate_gap"] == 1.0 and rep["offer_rate_gap_ci95"][0] > 0
    assert rep["treatment_difference_detected"] is True
    assert "detected" in rep["interpretation"]


def test_report_no_difference_when_treatment_is_equal():
    pairs = af.build_pairs(_base(30), n_pairs=30)

    def fair(member):
        return {"offer_kind": "discount", "offer_terms": {"pct": 20}, "escalated": False, "outcome": "saved"}

    rep = af.report(af.measure(pairs, fair))
    assert rep["sufficient_sample"] is True
    assert rep["offer_rate_gap"] == 0.0 and rep["offer_rate_gap_ci95"][0] <= 0 <= rep["offer_rate_gap_ci95"][1]
    assert rep["treatment_difference_detected"] is False


def test_thin_sample_is_reported_not_judged():
    """Below the minimum, even a stark gap is NOT called differential treatment — the harness
    reports the numbers but refuses to assert a fairness verdict on noise."""
    pairs = af.build_pairs(_base(5), n_pairs=5)

    def biased(member):
        return ({"offer_kind": "discount", "offer_terms": {"pct": 20}, "escalated": False, "outcome": "saved"}
                if member["group"] == "group_a"
                else {"offer_kind": None, "offer_terms": None, "escalated": True, "outcome": "lost"})

    rep = af.report(af.measure(pairs, biased))
    assert rep["sufficient_sample"] is False and rep["min_group_n"] == 5
    assert rep["treatment_difference_detected"] is False        # not judged on a thin sample
    assert "insufficient sample" in rep["interpretation"]
