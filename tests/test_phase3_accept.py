"""F1 — the acceptance scripts were outside the reach of BOTH verification gates.

`scripts/phase3_accept.py` advertises the agent-treatment fairness harness, and `README.md`
names it as one of the two ways fairness is monitored. The harness had NEVER EXECUTED:
`_fairness_bases` selected `s.tenure_months` from `subscriptions`, and that column lives on
`customers`, so the first call raised OperationalError.

WHY NEITHER GATE COULD EVER HAVE CAUGHT IT: nothing in tests/ imported `phase3_accept`, and no
mutant in scripts/mutate.py targeted it. A 100%-non-functional control sat behind two green
gates for as long as it shipped. THIS FILE EXISTS PRIMARILY TO IMPORT THAT MODULE — without an
importer, any fix to it is protected by the same nothing that let the defect ship.

Offline: `main()` spends real money and is never called here. The one function that reaches the
live agent (`_run_agent_for_fairness`) is exercised with `runtime` stubbed, so the DB
snapshot/restore glue around it is covered without an API call. The temp DB is built with
`synth.generate`, never the committed `keel.db` — scripts/mutate.py copies the tree excluding
`keel.db*`, so a test reading it would skip under the mutation harness and turn its baseline
red.
"""

from __future__ import annotations

import pytest

import db
import synth
from agent import runtime
from evals import agent_fairness

# THE IMPORT THAT DID NOT EXIST. Everything below is secondary to this line.
from scripts import phase3_accept


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "keel_test.db"))
    db.init_db(c)
    synth.generate(c)
    yield c
    c.close()


def test_fairness_bases_returns_populated_rows(conn):
    """The F1 defect itself. This raised OperationalError before the join landed, so the
    harness produced nothing at all — not a degraded report, no report."""
    bases = phase3_accept._fairness_bases(conn, agent_fairness.MIN_PAIRS)

    assert len(bases) == agent_fairness.MIN_PAIRS
    for b in bases:
        assert b["customer_id"] is not None
        assert b["account"]["plan"] and b["account"]["price"] is not None
        # tenure is the column that was being selected off the wrong table
        assert isinstance(b["account"]["tenure"], int)
        assert b["churn_reason"] and b["opening_message"]


def test_tenure_comes_from_customers_and_matches_that_row(conn):
    """A join can return rows and still return the WRONG rows. Checked against the source
    table rather than against "it did not raise"."""
    bases = phase3_accept._fairness_bases(conn, 5)
    for b in bases:
        expected = conn.execute("SELECT tenure_months FROM customers WHERE id = ?",
                                (b["customer_id"],)).fetchone()
        assert expected is not None, f"customer {b['customer_id']} does not exist"
        assert b["account"]["tenure"] == expected["tenure_months"]


def test_the_harness_runs_end_to_end_and_emits_a_populated_report(conn):
    """The artifact F1 is closed on: bases -> pairs -> measurements -> a report with real
    numbers in it. "It no longer raises" is the report; this is the artifact.

    The agent is STUBBED here, so this proves the harness pipeline executes — not that the
    live agent is fair. The live-agent leg costs money and is run separately."""
    bases = phase3_accept._fairness_bases(conn, agent_fairness.MIN_PAIRS)
    pairs = agent_fairness.build_pairs(bases, agent_fairness.MIN_PAIRS)
    assert len(pairs) == agent_fairness.MIN_PAIRS

    def stub_agent(member):
        return {"offer_kind": "discount", "offer_terms": {"pct": 20},
                "escalated": False, "outcome": "saved"}

    report = agent_fairness.report(agent_fairness.measure(pairs, stub_agent))
    assert report["groups"] == ["group_a", "group_b"]
    assert report["sufficient_sample"] is True
    assert report["min_group_n"] == agent_fairness.MIN_PAIRS
    for gap in ("offer_rate_gap", "mean_offer_value_gap",
                "escalation_rate_gap", "save_rate_gap"):
        assert gap in report, f"{gap} missing — the verdict reads all four"
    assert report["interpretation"]


def test_the_pairs_differ_only_in_the_proxy_on_REAL_customer_rows(conn):
    """build_pairs is unit-tested on synthetic dicts elsewhere. This is the same invariant
    over rows the join actually returned, which is where a bad join would show up."""
    pairs = agent_fairness.build_pairs(
        phase3_accept._fairness_bases(conn, agent_fairness.MIN_PAIRS),
        agent_fairness.MIN_PAIRS)
    for p in pairs:
        a, b = p["members"]["group_a"], p["members"]["group_b"]
        assert a["account"] == b["account"]
        assert a["customer_id"] == b["customer_id"]
        assert a["opening_message"] != b["opening_message"]


def test_running_one_pair_member_restores_the_world_it_borrowed(conn, monkeypatch):
    """Both members of a pair share a customer_id and group_a always runs first, so a
    group_a turn that presents an offer sets last_save_offer_days = 0 and the cooldown then
    denies group_b any offer — a gap the harness created itself, always in the same
    direction. _run_agent_for_fairness snapshots and restores around every member.

    runtime is stubbed, so this covers the snapshot/restore glue with no API call."""
    cid = phase3_accept._fairness_bases(conn, 1)[0]["customer_id"]
    conn.execute("UPDATE subscriptions SET last_save_offer_days = NULL WHERE customer_id = ?",
                 (cid,))
    conn.commit()

    def fake_new_session(customer_id, connection):
        # mutate the world exactly as a presented offer would
        connection.execute(
            "UPDATE subscriptions SET last_save_offer_days = 0 WHERE customer_id = ?",
            (customer_id,))
        connection.commit()
        return {"rec": {"offers": [], "escalated": False}, "outcome": "lost"}

    monkeypatch.setattr(runtime, "new_session", fake_new_session)
    monkeypatch.setattr(runtime, "live_turn", lambda session, message, connection: None)

    result = phase3_accept._run_agent_for_fairness(
        {"customer_id": cid, "opening_message": "Your price is too high."}, conn)

    assert set(result) == {"offer_kind", "offer_terms", "escalated", "outcome"}
    after = conn.execute(
        "SELECT last_save_offer_days FROM subscriptions WHERE customer_id = ?",
        (cid,)).fetchone()["last_save_offer_days"]
    assert after is None, "the member's turn leaked state into the next member's world"
