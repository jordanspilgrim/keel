"""Regression tests for the money-demo integrity properties the sixth review flagged:
- H3: both arms of the paired comparison must run from a byte-identical starting world
  (snapshot → restore → identical hash), so eligibility is a held constant, not a
  hidden confound.
- M2: the run-scoped intervention signal the manifest cites must survive a SECOND
  analytics pass — the post-run re-cluster must not delete it.
No network: these exercise the deterministic snapshot/restore + signal-lineage plumbing.
"""

from __future__ import annotations

import pytest

import config
import db
import synth
import run_demo
from analytics import themes


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    synth.generate(c)
    yield c
    c.close()


# --- H3: identical starting world across arms ------------------------------
def test_restore_world_is_byte_identical(conn):
    customer_ids = [r["customer_id"] for r in
                    conn.execute("SELECT customer_id FROM subscriptions ORDER BY customer_id LIMIT 30").fetchall()]
    world0 = run_demo._snapshot_world(conn, customer_ids)
    sha0 = run_demo._world_hash(world0)

    # simulate what the BASELINE arm can do: an accepted save resets a customer's
    # cooldown to 0, and a seeded-ineligible customer would otherwise stay changed
    conn.execute("UPDATE subscriptions SET last_save_offer_days = 0 WHERE customer_id = ?", (customer_ids[0],))
    conn.commit()
    assert run_demo._world_hash(run_demo._snapshot_world(conn, customer_ids)) != sha0  # world moved

    # restoring the snapshot returns the world to byte-identical starting state
    run_demo._restore_world(conn, world0)
    sha_after = run_demo._world_hash(run_demo._snapshot_world(conn, customer_ids))
    assert sha_after == sha0  # the after arm starts from the same world the baseline arm did
    restored = conn.execute("SELECT last_save_offer_days FROM subscriptions WHERE customer_id=?",
                            (customer_ids[0],)).fetchone()["last_save_offer_days"]
    assert restored == world0[0]["last_save_offer_days"]  # the mutation was undone exactly


def test_world_hash_is_order_independent_and_reproducible(conn):
    ids = [r["customer_id"] for r in
           conn.execute("SELECT customer_id FROM subscriptions ORDER BY customer_id LIMIT 10").fetchall()]
    # snapshot orders by customer_id, so two snapshots of the same world hash identically
    assert run_demo._world_hash(run_demo._snapshot_world(conn, ids)) \
        == run_demo._world_hash(run_demo._snapshot_world(conn, list(reversed(ids))))


def test_persist_refuses_saved_plus_cancelled(conn):
    """H1 invariant: a conversation cannot be both saved and cancellation-routed."""
    import pytest
    from agent import runtime
    with pytest.raises(ValueError):
        runtime.persist_conversation(conn, {
            "customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "x"}],
            "disposition": {"outcome": "saved"}, "outcome": "saved", "offer_made": "20% discount",
            "evidence": {}, "guardrail_events": [], "audit": [], "cancellation_routed": True})


def test_batch_is_terminal_after_routed_cancellation(conn, monkeypatch):
    """H1: once the batch agent routes a cancellation it does NOT run again and the
    simulator is not re-asked — the same terminal semantics as the live path, so no
    saved-after-cancellation record can arise."""
    import sim
    from agent import runtime
    calls = {"sim": 0}

    def fake_advance(pending, transcript, input_list, cid, sub, conn, rec, system=runtime.SYSTEM, on_step=None):
        rec["cancellation_routed"] = True
        transcript.append({"role": "assistant", "content": "I'll pass your cancellation to our team."})
        return "…"
    monkeypatch.setattr(runtime, "_advance", fake_advance)
    monkeypatch.setattr(sim, "respond",
                        lambda *a, **k: (calls.__setitem__("sim", calls["sim"] + 1), {"decision": "continue", "reply": "x"})[1])
    monkeypatch.setattr(runtime, "_disposition", lambda *a: {"outcome": "lost", "offer_made": None})
    rec = runtime.simulate_conversation(
        {"id": None, "customer_id": 1, "opening_message": "cancel me", "churn_reason": "price"}, conn)
    assert rec["outcome"] == "lost" and rec["cancellation_routed"] is True
    assert calls["sim"] == 0  # simulator never re-asked → terminal, no re-entry


def test_init_db_migrates_legacy_escalation_requests(tmp_path):
    """M1: an existing DB whose escalation_requests table predates session_key must gain the
    column BEFORE idx_escalation_session_key is built — init_db must not fail with 'no such
    column: session_key', and must be safely re-runnable."""
    c = db.connect(str(tmp_path / "legacy.db"))
    # a pre-column escalation_requests table, exactly as an older build shipped
    c.executescript("CREATE TABLE escalation_requests (id INTEGER PRIMARY KEY, "
                    "conversation_id INTEGER, reason TEXT, status TEXT, created_at TEXT);")
    c.commit()
    db.init_db(c)   # must migrate + index without raising
    db.init_db(c)   # idempotent
    cols = {r["name"] for r in c.execute("PRAGMA table_info(escalation_requests)")}
    assert "session_key" in cols
    c.close()


def test_save_queues_a_durable_fulfillment_record(conn):
    """M3: an accepted offer (a save) queues a durable offer_fulfillment_requests work item,
    so the server's 'our team will apply it' is backed by a real pending action — while a
    lost conversation queues nothing."""
    from agent import runtime
    runtime.persist_conversation(conn, {
        "customer_id": 1, "scenario_id": None,
        "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "too expensive"}, {"role": "assistant", "content": "ok"}],
        "disposition": {"outcome": "saved", "offer_made": "3-month pause"},
        "outcome": "saved", "offer_made": "3-month pause", "evidence": {},
        "guardrail_events": [], "audit": []})
    row = conn.execute("SELECT offer, status, conversation_id FROM offer_fulfillment_requests").fetchone()
    assert row is not None and row["offer"] == "3-month pause" and row["status"] == "pending_application"
    runtime.persist_conversation(conn, {
        "customer_id": 1, "scenario_id": None,
        "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "bye"}], "disposition": {"outcome": "lost"},
        "outcome": "lost", "offer_made": None, "evidence": {}, "guardrail_events": [], "audit": []})
    assert conn.execute("SELECT count(*) FROM offer_fulfillment_requests").fetchone()[0] == 1  # only the save


def test_reset_db_clears_cancellation_requests(conn):
    """A cancellation_requests row (FK → conversations) must not block reset_db's
    FK-ordered deletes — the demo re-seeds a DB that already holds routed cancellations."""
    conn.execute("INSERT INTO conversations (customer_id, transcript_json, outcome, created_at) "
                 "VALUES (1, '[]', 'lost', 't')")
    cid = conn.execute("SELECT id FROM conversations ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.execute("INSERT INTO cancellation_requests (conversation_id, status, channel, created_at) "
                 "VALUES (?,?,?,?)", (cid, "pending_human", "email", "t"))
    conn.commit()
    db.reset_db(conn)  # must not raise sqlite3.IntegrityError on the FK
    assert conn.execute("SELECT count(*) FROM cancellation_requests").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


# --- M5: analytics readers use the frozen conversation-time price ----------
def _persist_saved(conn, scenario_id, run="run-M5", phase="baseline"):
    from agent import runtime
    runtime.persist_conversation(conn, {
        "customer_id": 1, "scenario_id": scenario_id,
        "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "too expensive"}, {"role": "assistant", "content": "hi"}],
        "disposition": {"outcome": "saved", "offer_made": "20% discount", "churn_reason": "Price too high"},
        "outcome": "saved", "offer_made": "20% discount", "evidence": {},
        "guardrail_events": [], "audit": [], "run_id": run, "phase": phase})


def test_segment_metrics_use_frozen_conversation_price(conn):
    """M5: margin-adjusted segment lift is computed on the price the customer was on at
    conversation time, so a later subscription price change cannot rewrite the demo's
    reported number. (Scenario 1 is customer 1 / 'Price too high' / $499.)"""
    _persist_saved(conn, scenario_id=1)
    before = run_demo._segment_metrics(conn, "Price too high", run_id="run-M5", phase="baseline")
    assert before["n"] == 1 and before["madj_save_rate"] == pytest.approx(1 - 0.20, abs=1e-6)
    conn.execute("UPDATE subscriptions SET price = 50 WHERE customer_id = 1")
    conn.commit()
    after = run_demo._segment_metrics(conn, "Price too high", run_id="run-M5", phase="baseline")
    assert after["madj_save_rate"] == before["madj_save_rate"]  # history did not move


def test_conversation_views_use_frozen_conversation_price(conn):
    """M5: the VoC view's realized margin_cost is likewise anchored to the frozen price,
    not the current subscription price."""
    _persist_saved(conn, scenario_id=1)
    v0 = next(v for v in themes.build_conversation_views(conn))
    assert v0["price"] == 499.0 and v0["margin_cost"] == pytest.approx(499.0 * 0.20, abs=1e-6)
    conn.execute("UPDATE subscriptions SET price = 50 WHERE customer_id = 1")
    conn.commit()
    v1 = next(v for v in themes.build_conversation_views(conn))
    assert v1["price"] == 499.0 and v1["margin_cost"] == v0["margin_cost"]  # frozen


# --- pre-registered median-of-k is an honest estimator, not cherry-picking -
def test_run_median_reports_median_not_max_and_keeps_low_draws(monkeypatch, tmp_path):
    """The median-of-k estimator must report the MEDIAN (not the max) and must include
    every draw — the low/zero/NEGATIVE ones too. Dropping unfavorable draws or reporting
    the best run would be exactly the metric-rigging the demo forbids. No network: run_once
    is stubbed with a fixed spread."""
    import json
    (tmp_path / "dashboard").mkdir()
    monkeypatch.chdir(tmp_path)
    draws = [-4.0, 2.0, 7.0, 20.0, 33.0]          # median 7.0 — NOT the 33.0 max; incl a negative
    seq = iter(draws)

    def fake_run_once(conn, run_id=None):
        v = next(seq)
        man = {"run_id": f"run-{v}", "treated_cohort_n": 60,
               "baseline": {"segment_save_rate": 0.27, "overall_save_rate": 0.26},
               "after": {"segment_save_rate": round(0.27 + v / 100, 4), "overall_save_rate": 0.29,
                         "eval_pass_rate": 0.85},
               "lift": {"segment_save_pp": v, "segment_madj_pp": round(v * 0.8, 1), "overall_save_pp": 2.0},
               "outcome_parity_gap": 0.02}
        return {"manifest": man, "data": {"provenance": {"run_id": man["run_id"]}}}

    exported = {}  # capture what the dashboard is re-rendered from
    _real_connect = db.connect
    monkeypatch.setattr(run_demo, "run_once", fake_run_once)
    monkeypatch.setattr(run_demo.export, "write_data", lambda data, **k: exported.update(data))
    monkeypatch.setattr(run_demo.db, "connect", lambda *a, **k: _real_connect(str(tmp_path / "m.db")))
    assert run_demo.run_median(k=5) == 0

    agg = json.loads((tmp_path / "dashboard" / "demo_aggregate.json").read_text())
    assert agg["segment_save_pp"]["median"] == 7.0                       # median, not the max
    assert agg["segment_save_pp"]["min"] == -4.0 and agg["segment_save_pp"]["max"] == 33.0
    assert agg["segment_save_pp"]["values"] == draws                     # every draw kept (incl negative)
    assert agg["committed_run_id"] == "run-7.0" and agg["k"] == 5
    man = json.loads((tmp_path / "dashboard" / "manifest.json").read_text())
    assert man["lift"]["segment_save_pp"] == 7.0                         # committed manifest IS the median run
    # the dashboard is re-rendered from the COMMITTED (median) run — not whatever ran last
    assert exported["provenance"]["run_id"] == "run-7.0"


def test_run_median_rejects_even_k(monkeypatch, tmp_path):
    """R11: even k has no real median run, so the committed manifest would diverge from the
    reported aggregate median — reject it rather than ship divergent artifacts."""
    import pytest as _pytest
    (tmp_path / "dashboard").mkdir()
    monkeypatch.chdir(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(run_demo, "run_once", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with _pytest.raises(SystemExit):
        run_demo.run_median(k=4)
    assert called["n"] == 0  # rejected before running anything


def test_run_median_returns_nonzero_on_nonpositive_median(monkeypatch, tmp_path):
    """H5: a flat/negative median is NOT success — run_median returns a nonzero exit code so
    CI or a demo script can't read a dead flywheel as a pass. Every draw is still recorded."""
    import json
    (tmp_path / "dashboard").mkdir()
    monkeypatch.chdir(tmp_path)
    draws = [-5.0, -4.0, -3.0, -2.0, -1.0]      # median -3.0
    seq = iter(draws)

    def fake_run_once(conn, run_id=None):
        v = next(seq)
        man = {"run_id": f"run-{v}", "treated_cohort_n": 60,
               "baseline": {"segment_save_rate": 0.30, "overall_save_rate": 0.30},
               "after": {"segment_save_rate": round(0.30 + v / 100, 4), "overall_save_rate": 0.29,
                         "eval_pass_rate": 0.85},
               "lift": {"segment_save_pp": v, "segment_madj_pp": v, "overall_save_pp": v},
               "outcome_parity_gap": 0.02}
        return {"manifest": man, "data": {"provenance": {"run_id": man["run_id"]}}}

    _real = db.connect
    monkeypatch.setattr(run_demo, "run_once", fake_run_once)
    monkeypatch.setattr(run_demo.export, "write_data", lambda *a, **k: None)
    monkeypatch.setattr(run_demo.db, "connect", lambda *a, **k: _real(str(tmp_path / "m.db")))
    assert run_demo.run_median(k=5) == 1                             # nonpositive median → nonzero exit
    agg = json.loads((tmp_path / "dashboard" / "demo_aggregate.json").read_text())
    assert agg["segment_save_pp"]["median"] == -3.0 and agg["segment_save_pp"]["values"] == draws  # honest


def test_run_median_restores_committed_run_as_canonical_db(monkeypatch, tmp_path):
    """H5: the committed (median) run's DB is restored as the canonical Explorer/API database,
    so the live surfaces read the SAME run the dashboard renders — not whichever ran LAST."""
    canonical = tmp_path / "canonical.db"
    monkeypatch.setattr(run_demo.config, "DB_PATH", str(canonical))
    (tmp_path / "dashboard").mkdir()
    monkeypatch.chdir(tmp_path)
    draws = [5.0, 1.0, 9.0]                     # median 5.0 → committed run-5.0; LAST run is run-9.0
    seq = iter(draws)

    def fake_run_once(conn, run_id=None):
        v = next(seq)
        rid = f"run-{v}"
        canonical.write_text(rid)               # each run leaves its id as the DB "content"
        man = {"run_id": rid, "treated_cohort_n": 60,
               "baseline": {"segment_save_rate": 0.30, "overall_save_rate": 0.30},
               "after": {"segment_save_rate": round(0.30 + v / 100, 4), "overall_save_rate": 0.31,
                         "eval_pass_rate": 0.85},
               "lift": {"segment_save_pp": v, "segment_madj_pp": v, "overall_save_pp": v},
               "outcome_parity_gap": 0.02}
        return {"manifest": man, "data": {"provenance": {"run_id": rid}}}

    _real = db.connect
    monkeypatch.setattr(run_demo, "run_once", fake_run_once)
    monkeypatch.setattr(run_demo.export, "write_data", lambda *a, **k: None)
    monkeypatch.setattr(run_demo.db, "connect", lambda *a, **k: _real(str(tmp_path / "m.db")))
    run_demo.run_median(k=3)
    assert canonical.read_text() == "run-5.0"   # canonical DB = committed (median), not the last (run-9.0)


def test_run_median_aborts_if_any_run_bails(monkeypatch, tmp_path):
    """A structural bail in any run aborts the whole estimate — no partial median over
    fewer than k runs (which would silently change the denominator)."""
    import pytest as _pytest
    (tmp_path / "dashboard").mkdir()
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def fake_run_once(conn, run_id=None):
        calls["n"] += 1
        if calls["n"] == 3:
            return None
        man = {"run_id": "r", "treated_cohort_n": 60,
               "baseline": {"segment_save_rate": 0.27, "overall_save_rate": 0.26},
               "after": {"segment_save_rate": 0.30, "overall_save_rate": 0.29, "eval_pass_rate": 0.85},
               "lift": {"segment_save_pp": 3.0, "segment_madj_pp": 2.0, "overall_save_pp": 2.0},
               "outcome_parity_gap": 0.02}
        return {"manifest": man, "data": {"provenance": {"run_id": "r"}}}

    _real_connect = db.connect
    monkeypatch.setattr(run_demo, "run_once", fake_run_once)
    monkeypatch.setattr(run_demo.db, "connect", lambda *a, **k: _real_connect(str(tmp_path / "m.db")))
    with _pytest.raises(SystemExit):
        run_demo.run_median(k=5)
    assert not (tmp_path / "dashboard" / "demo_aggregate.json").exists()  # no partial artifact written


# --- H1: Measure governs Learn — only eval-eligible baseline steers the signal ---
def test_recommend_intervention_gates_learn_on_eval_eligibility(conn):
    """H1: an ungraded/failed/error baseline conversation must NOT influence the intervention
    ranking — only conversations that PASSED the current eval spec count, and the signal
    records that eligibility. Without gating, the failed/ungraded ones would count."""
    from evals import judge
    from agent import runtime
    ver = judge.EVAL_SPEC_VERSION

    def _mk():  # a baseline 'Price too high' conversation (scenario 1 = customer 1), all lost
        runtime.persist_conversation(conn, {
            "customer_id": 1, "scenario_id": 1,
            "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "too expensive"}, {"role": "assistant", "content": "hi"}],
            "disposition": {"outcome": "lost", "offer_made": None, "churn_reason": "Price too high"},
            "outcome": "lost", "offer_made": None, "evidence": {},
            "guardrail_events": [], "audit": [], "run_id": "R", "phase": "baseline"})
        return conn.execute("SELECT id FROM conversations ORDER BY id DESC LIMIT 1").fetchone()["id"]

    def _grade(cid, verdict):
        conn.execute("INSERT INTO evals (conversation_id, scores_json, verdict, rubric_version, created_at) "
                     "VALUES (?,?,?,?,?)", (cid, "{}", verdict, ver, "t"))

    a, b, c, _d = _mk(), _mk(), _mk(), _mk()          # 4 baseline conversations; _d left UNGRADED
    _grade(a, "pass"); _grade(b, "pass"); _grade(c, "fail")
    conn.commit()

    gated = themes.recommend_intervention(conn, run_id="R", phase="baseline", eligible_only=True)
    assert gated["segment"] == "Price too high"
    assert gated["evidence"]["n"] == 2                 # only the 2 PASSING conversations counted
    el = gated["eval_eligibility"]
    assert el["total"] == 4 and el["eligible"] == 2 and el["excluded"] == 2 and el["graded"] == 3
    assert el["coverage"] == 0.75 and el["rule"] == "current-spec eval verdict = pass"

    ungated = themes.recommend_intervention(conn, run_id="R", phase="baseline", eligible_only=False)
    assert ungated["evidence"]["n"] == 4               # ungated, the failed + ungraded ones count too


# --- M2: cited experiment signal survives a post-run re-cluster ------------
def test_experiment_signal_survives_reclustering(conn):
    signal = {"segment": "Price too high", "recommended_lever": "discount", "evidence": {"loss": 4.2}}
    sid = themes.persist_signal(conn, signal, run_id="run-TEST")
    assert themes.load_signal(conn, sid) is not None

    # the post-run re-cluster writes fresh EPHEMERAL dashboard signals (run_id NULL)…
    themes.persist(conn,
                   [{"label": "L", "summary": "s", "size": 3, "save_rate": 0.5,
                     "avg_margin_cost": 1.0, "example_ids": [1]}],
                   [{"label": "L", "recommendation": "r", "priority_score": 1.5}])

    # …and the run-scoped experiment signal the manifest cites is STILL deref-able
    reloaded = themes.load_signal(conn, sid)
    assert reloaded is not None and reloaded["segment"] == "Price too high"
    assert conn.execute("SELECT count(*) FROM signals WHERE run_id IS NULL").fetchone()[0] >= 1
    assert conn.execute("SELECT count(*) FROM signals WHERE run_id='run-TEST'").fetchone()[0] == 1
