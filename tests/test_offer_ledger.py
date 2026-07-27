"""Tests for the offer ledger, the persisted eval envelope, and the safety gate —
the invariants the third review found untested. No real API calls.

These prove the architectural fixes hold as DATA invariants, not just in prose:
exact terms survive persistence, one offer is active at a time, a below-ceiling
presented offer is costed at the presented (not authorized) terms, and the
guardrail-health gate honours version + freshness.
"""

from __future__ import annotations

import json

import pytest

import config
from agent import guardrails
import db
import synth
from agent import offers, runtime, safety
from evals import run_evals


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    synth.generate(c)
    yield c
    c.close()


# --- offer ledger ----------------------------------------------------------
def test_multiple_offers_may_be_authorized_one_presented():
    led: list[offers.Offer] = []
    d = offers.authorize(led, "discount", {"pct": 10})
    p = offers.authorize(led, "pause", {"months": 3})
    # both remain authorized during a negotiation (the agent may explore both)
    assert [o.state for o in led] == ["authorized", "authorized"]
    # presenting one supersedes any OTHER presented offer → at most one presented
    offers.present(led, d, {"pct": 10})
    offers.present(led, p, {"months": 3})
    assert p.state == "presented" and d.state == "superseded"
    assert offers.offer_of_kind(led, "pause") is p


def test_rejected_and_unpresented_accessors():
    from agent import offers
    led = []
    d = offers.authorize(led, "discount", {"pct": 20})
    p = offers.authorize(led, "pause", {"months": 3})
    # both authorized, neither presented → the newest un-declined authorized offer
    assert offers.unpresented_new_authorized(led) is p
    assert offers.rejected_of_kind(led, "discount") is None
    # decline the discount → it's no longer a candidate, and it reads as rejected
    d.state, d.presented_terms = "presented", {"pct": 20}
    offers.mark_rejected(led)
    assert offers.rejected_of_kind(led, "discount") is d
    # unpresented_new_authorized skips the declined kind, returns the pause
    assert offers.unpresented_new_authorized(led) is p
    # decline the pause too → no un-declined authorized offer remains
    p.state, p.presented_terms = "presented", {"months": 3}
    offers.mark_rejected(led)
    assert offers.unpresented_new_authorized(led) is None


def test_offer_summary_prefers_presented_then_accepted():
    led: list[offers.Offer] = []
    o = offers.authorize(led, "discount", {"pct": 20})
    o.state, o.presented_terms = "presented", {"pct": 15}      # presented LESS than the 20 ceiling
    assert offers.offer_summary(led) == "15% discount"          # not 20%
    offers.mark_accepted(led)
    assert offers.accepted(led) is o and offers.offer_summary(led) == "15% discount"


def test_rejection_transitions_presented_to_rejected():
    led: list[offers.Offer] = []
    o = offers.authorize(led, "discount", {"pct": 20})
    offers.present(led, o, {"pct": 20})
    offers.mark_rejected(led)
    assert o.state == "rejected"
    # a rejected offer was still EXTENDED — it shows in the offer summary (cooldown/analytics)
    assert offers.extended(led) is o and offers.offer_summary(led) == "20% discount"
    # …but it is no longer 'presented' (a faithful state machine)
    assert offers.presented(led) is None


def test_terms_within_ceiling():
    assert offers.terms_within({"pct": 10}, {"pct": 20}, "discount")
    assert not offers.terms_within({"pct": 25}, {"pct": 20}, "discount")
    assert offers.terms_within({"months": 2}, {"months": 3}, "pause")


def test_discounts_are_whole_percents_no_tolerance():
    """M4: a fractional discount is rejected outright — no ceiling+0.5 tolerance that
    would pass validation and then be rounded away in rendering/economics."""
    assert not offers.terms_within({"pct": 20.49}, {"pct": 20}, "discount")
    assert not offers.terms_within({"pct": 20.5}, {"pct": 20}, "discount")
    assert offers.terms_within({"pct": 20}, {"pct": 20}, "discount")   # exact ceiling ok
    assert not offers.terms_within({"months": 1.5}, {"months": 3}, "pause")


def test_policy_floors_a_fractional_discount_to_whole_percent(monkeypatch):
    from agent import policy
    monkeypatch.setattr(policy, "DISCOUNTS_ENABLED", True)
    v = policy.authorize("offer_discount", {"pct": 18.9},
                         {"plan": "Pro", "price": 99.0, "last_save_offer_days": None})
    assert v["allowed"] and v["args"]["pct"] == 18   # floored, never rounded up past intent
    assert not offers.terms_within({"months": 4}, {"months": 3}, "pause")


# --- eval envelope survives persistence (the H1 regression) ----------------
def test_authorized_terms_survive_persistence_and_reload(conn):
    rec = runtime._new_rec()
    # authorize 10% (a policy cap of a larger ask), present exactly 10%
    o = offers.authorize(rec["offers"], "discount", {"pct": 10})
    o.state, o.presented_terms = "presented", {"pct": 10}
    rec["tool_facts"].append({"tool": "get_subscription", "call_id": "c1",
                              "result": {"plan": "Pro", "price": 99.0}})
    rec["policy_decisions"].append({"tool": "offer_discount", "action": "ok",
                                    "args": {"pct": 10}, "reason": "within policy"})
    record = {"customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "assistant", "content": "hi"}],
              "disposition": {"outcome": "saved", "offer_made": "10% discount"}, "outcome": "saved",
              "offer_made": runtime._offer_made(rec), "evidence": runtime._evidence(rec),
              "guardrail_events": [], "audit": []}
    cid = runtime.persist_conversation(conn, record)

    convo = run_evals.build_judge_input(conn, cid)
    assert convo["offers"][0]["authorized_terms"] == {"pct": 10}   # EXACT 10%, not a lossy tool:action
    assert convo["offers"][0]["presented_terms"] == {"pct": 10}
    assert convo["tool_facts"][0]["result"]["price"] == 99.0        # tool facts reach the judge


# --- safety gate: version + freshness (the M3 regression) ------------------
def test_missing_health_is_advisory_not_gating(conn):
    st = safety.program_state(conn)
    assert st["metrics"]["guardrail_catch_rate"] is None
    assert any("not validated" in a for a in st["advisories"])
    assert st["healthy"] is True                                   # console usable out of the box


def test_stale_version_health_forces_safe_mode(conn):
    db.record_health(conn, "guardrail_catch_rate", 1.0, "old", version="0")  # not the current version
    st = safety.program_state(conn)
    assert st["healthy"] is False and st["mode"] == "safe"
    assert any("version" in r for r in st["reasons"])


def test_below_floor_health_forces_safe_mode(conn):
    db.record_health(conn, "guardrail_catch_rate", 0.50, "bad", version=guardrails.guardrail_version())
    st = safety.program_state(conn)
    assert st["healthy"] is False and any("catch rate" in r for r in st["reasons"])


def test_current_healthy_signal_stays_normal(conn):
    db.record_health(conn, "guardrail_catch_rate", 1.0, "good", version=guardrails.guardrail_version())
    st = safety.program_state(conn)
    assert st["healthy"] is True and st["mode"] == "normal"


# --- eval-spec versioning + one-grade-per-spec (M4/R4-B) --------------------
def test_eval_spec_version_is_a_content_hash():
    from evals import judge
    assert judge.EVAL_SPEC_VERSION.startswith("spec-") and len(judge.EVAL_SPEC_VERSION) > 8
    assert judge._spec_version() == judge.EVAL_SPEC_VERSION  # deterministic


def test_eval_spec_version_hashes_the_actual_formatter_source(monkeypatch):
    """M3: the spec version is derived from the SOURCE of the prompt formatter, not a
    hand-maintained marker string — so a change to how the judge prompt is rendered
    yields a new version automatically (no one has to remember to bump anything)."""
    import inspect
    from evals import judge
    base = judge._spec_version()
    # simulate a formatter edit by making inspect.getsource report changed source
    real = inspect.getsource
    monkeypatch.setattr(judge.inspect, "getsource",
                        lambda fn: real(fn) + "\n# edited prompt rendering\n")
    assert judge._spec_version() != base  # the version tracked the formatter change


def test_eval_spec_hash_includes_verdict_and_input_builder(monkeypatch):
    """M2: the spec version must change if EITHER the mechanical verdict (derive_verdict)
    or the input-envelope serializer (build_judge_input) changes — both define a grade."""
    from evals import judge
    real = judge.inspect.getsource
    for target in (judge.derive_verdict, judge.build_judge_input):
        base = judge._spec_version()
        monkeypatch.setattr(judge.inspect, "getsource",
                            lambda fn, t=target, r=real: r(fn) + ("\n# edited\n" if fn is t else ""))
        assert judge._spec_version() != base
        monkeypatch.undo()


def test_judge_prompt_includes_policy_decisions(monkeypatch):
    """M3: the policy_decisions evidence (loaded into the envelope) is actually RENDERED
    into the judge prompt, with the adjusted terms — not silently dropped."""
    from evals import judge
    captured = {}
    monkeypatch.setattr(judge.llm, "structured",
                        lambda model, instr, user, schema, name, **k: captured.setdefault("user", user)
                        or {"scores": {d: 4 for d in judge.RUBRIC}, "rationale": "x", "fairness_flag": False})
    convo = {"transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "assistant", "content": "hi"}], "disposition": {"outcome": "saved"},
             "offers": [], "tool_facts": [],
             "policy_decisions": [{"tool": "extend_offer", "action": "adjust",
                                   "args": {"pct": 20}, "reason": "capped to ceiling"}]}
    judge.judge_conversation(convo)
    assert "POLICY DECISIONS" in captured["user"]
    assert "extend_offer→adjust" in captured["user"] and "pct" in captured["user"]


def test_one_grade_per_conversation_and_spec(conn):
    import sqlite3
    from evals import judge
    cur = conn.execute(
        "INSERT INTO conversations (customer_id, scenario_id, transcript_json, disposition_json, "
        "offer_made, evidence_json, outcome, created_at) VALUES (1,NULL,'[]','{}',NULL,'{}','lost','t')")
    cid = cur.lastrowid
    ver = judge.EVAL_SPEC_VERSION
    conn.execute("INSERT INTO evals (conversation_id, scores_json, verdict, rubric_version, created_at) "
                 "VALUES (?,?,?,?,?)", (cid, "{}", "pass", ver, "t"))
    conn.commit()
    # a second grade under the SAME spec violates the unique index
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO evals (conversation_id, scores_json, verdict, rubric_version, created_at) "
                     "VALUES (?,?,?,?,?)", (cid, "{}", "fail", ver, "t"))
        conn.commit()


# --- experiment lineage: baseline + after coexist under one run_id (M6/R4-D) -
def test_run_phase_scoped_metrics(conn):
    from dashboard import export

    def _persist(outcome, phase, offer):
        rec = {"customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "assistant", "content": "hi"}],
               "disposition": {"outcome": outcome, "offer_made": offer}, "outcome": outcome,
               "offer_made": offer, "evidence": {}, "guardrail_events": [], "audit": [],
               "run_id": "run-X", "phase": phase}
        runtime.persist_conversation(conn, rec)

    # baseline: 0/2 saved; after: 2/2 saved — both under the SAME run_id, no DB reset
    _persist("lost", "baseline", None); _persist("lost", "baseline", None)
    _persist("saved", "after", "20% discount"); _persist("saved", "after", "20% discount")
    before = export.conversation_metrics(conn, run_id="run-X", phase="baseline")
    after = export.conversation_metrics(conn, run_id="run-X", phase="after")
    assert before["save_rate"] == 0.0 and after["save_rate"] == 1.0   # phases measured separately
    assert before["n"] == 2 and after["n"] == 2                       # baseline preserved, not wiped


def test_signal_persist_load_carries_run_id(conn):
    from analytics import themes
    sig = {"segment": "Price too high", "recommended_lever": "discount", "evidence": {"loss": 5.0}}
    sid = themes.persist_signal(conn, sig, run_id="run-Y")
    loaded = themes.load_signal(conn, sid)
    assert loaded["segment"] == "Price too high"
    assert conn.execute("SELECT run_id FROM signals WHERE id=?", (sid,)).fetchone()["run_id"] == "run-Y"


# --- batch honours the caller DB; in-memory is rejected, not silently wrong --
def test_batch_rejects_in_memory_connection():
    import batch
    mem = db.connect(":memory:")
    db.init_db(mem)
    with pytest.raises(ValueError):
        batch.run_batch(mem, [{"id": 1, "customer_id": 1, "opening_message": "hi"}])
    mem.close()


# --- persistence defensively redacts EVERY role (terminal sim reply included) -
def test_persistence_redacts_all_roles(conn):
    record = {"customer_id": 1, "scenario_id": None,
              "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "my card is 4111 1111 1111 1111"},
                             {"role": "assistant", "content": "noted"}],
              "disposition": {"outcome": "lost", "offer_made": None}, "outcome": "lost",
              "offer_made": None, "evidence": {}, "guardrail_events": [], "audit": []}
    cid = runtime.persist_conversation(conn, record)
    stored = conn.execute("SELECT transcript_json FROM conversations WHERE id=?", (cid,)).fetchone()[0]
    assert "4111" not in stored  # user turn scrubbed even without an upstream guarantee


def test_persisted_evidence_is_deidentified(conn):
    """H1: the eval envelope is de-identified BEFORE it is stored — the customer name
    from a read tool is dropped, and PII the model copied into a policy argument is
    recursively redacted — so the same conversation isn't identifiable in evidence_json
    while its transcript is scrubbed. Grounding fields (price, plan, …) survive."""
    rec = runtime._new_rec()
    rec["tool_facts"] = [
        {"tool": "get_customer", "call_id": "c1", "result": {"name": "Emma Nguyen", "segment": "SMB", "arpu": 99.0}},
        {"tool": "get_subscription", "call_id": "c2", "result": {"plan": "Pro", "price": 99.0}}]
    # a consequential-tool decision with a model-authored free-text arg containing a name
    rec["policy_decisions"] = [
        {"tool": "offer_discount", "action": "ok", "args": {"pct": 20}, "reason": "Within policy."},
        {"tool": "deny_refund", "action": "needs_human",
         "args": {"reason": "Refund for account holder Robert Johnson, email bob@x.com"}, "reason": "needs human"}]
    record = {"customer_id": 1, "scenario_id": None, "transcript": [{"role": "assistant", "content": config.AI_DISCLOSURE}, {"role": "user", "content": "hi"}],
              "disposition": {"outcome": "lost", "offer_made": None, "churn_reason": "It's Maria Garcia, too pricey"},
              "outcome": "lost", "offer_made": None, "evidence": runtime._evidence(rec),
              "guardrail_events": [], "audit": []}
    cid = runtime.persist_conversation(conn, record)
    row = conn.execute("SELECT evidence_json, disposition_json FROM conversations WHERE id=?", (cid,)).fetchone()
    stored, disp = row["evidence_json"], row["disposition_json"]
    assert "Emma Nguyen" not in stored          # name dropped by the per-tool allowlist
    assert "Robert Johnson" not in stored and "bob@x.com" not in stored  # model free-text policy arg dropped
    assert "Maria Garcia" not in disp           # disposition churn_reason redacted (L2)
    ev = json.loads(stored)
    assert ev["tool_facts"][1]["result"]["price"] == 99.0   # grounding field survives
    assert ev["policy_decisions"][0]["args"] == {"pct": 20}  # numeric arg kept
    assert ev["policy_decisions"][1]["args"] == {}           # free-text arg structurally dropped
    assert "name" not in ev["tool_facts"][0]["result"]


def test_grade_all_records_error_for_a_malformed_row_not_abort(conn, monkeypatch):
    """M1: a single malformed persisted row records a coverage-miss for THAT
    conversation; it must not abort the whole batch before any error row is written."""
    from evals import run_evals, judge
    monkeypatch.setattr(judge, "judge_conversation",
                        lambda cv, **k: {"scores": {d: 4 for d in judge.RUBRIC}, "rationale": "ok",
                                         "fairness_flag": False})
    conn.execute("INSERT INTO conversations (customer_id, transcript_json, disposition_json, outcome, "
                 "evidence_json, created_at) VALUES (1,'[]','{}','lost','{}','t')")
    conn.execute("INSERT INTO conversations (customer_id, transcript_json, disposition_json, outcome, "
                 "evidence_json, created_at) VALUES (1,'{ not valid json','{}','lost','{}','t')")
    conn.commit()
    run_evals.grade_all(conn)  # must NOT raise
    verdicts = [r["verdict"] for r in conn.execute(
        "SELECT verdict FROM evals WHERE rubric_version=?", (judge.EVAL_SPEC_VERSION,)).fetchall()]
    assert verdicts.count("error") == 1 and verdicts.count("pass") == 1


def test_evidence_allowlist_fails_closed_for_unknown_tool():
    """L1: an unregistered read tool persists NO fields (default ()), so adding a tool
    without updating the evidence map can't silently switch to allow-all."""
    fct = {"tool": "some_new_tool", "call_id": "c1", "result": {"secret": "x", "ssn": "123-45-6789"}}
    out = runtime._deidentify_tool_fact(fct)
    assert out["result"] == {}   # nothing retained for an unknown tool


# --- two offer tool calls both authorize; presenting enforces one (H2) -------
def test_two_offer_calls_authorize_both(conn):
    rec = runtime._new_rec()
    sub = {"plan": "Pro", "price": 99.0, "last_save_offer_days": None}
    runtime._resolve_call("offer_discount", {"pct": 10}, 1, sub, conn, rec)
    runtime._resolve_call("offer_pause", {"months": 2}, 1, sub, conn, rec)
    # both authorized (the agent explored both); neither presented yet
    assert {o.kind for o in rec["offers"]} == {"discount", "pause"}
    assert all(o.state == "authorized" for o in rec["offers"])
    # a contract offering the pause presents ONLY the pause
    runtime._apply_contract(
        {"offer": {"kind": "pause", "pct": None, "months": 2}, "account_facts": []}, rec)
    presented = [o for o in rec["offers"] if o.state == "presented"]
    assert len(presented) == 1 and presented[0].kind == "pause"


def test_batch_and_live_persist_identical_ledger_evidence_for_the_same_terminal(tmp_path, monkeypatch):
    """E2E#14 / RT22: resolve_session transitioned a dangling presented offer on a lost close
    and simulate_conversation did not, so the two paths persisted DIFFERENT ledger evidence
    for the identical terminal — batch 'presented' vs live 'rejected'. _offer_ledger_line
    renders that string verbatim into the judge prompt.

    This drives BOTH REAL PATHS. The earlier version of this test called the shared helper
    _close_dangling_offer twice in a loop and asserted the two results matched — a tautology
    over one pure function, which meant DELETING EITHER CALL SITE left the suite green. The
    property that actually matters is that both paths reach the helper."""
    import db as _db
    import synth as _synth
    from agent import guardrails, runtime

    conn = _db.connect(str(tmp_path / "parity.db"))
    _synth.generate(conn)
    monkeypatch.setattr(guardrails, "check_scope", lambda t: {"in_scope": True, "reason": "t"})
    monkeypatch.setattr(guardrails, "classify_injection", lambda t: False)
    monkeypatch.setattr(runtime, "_disposition",
                        lambda transcript, scenario, rec, outcome, accepted: {
                            "intent": "cancel", "churn_reason": "price",
                            "offer_made": runtime._offer_made(rec),
                            "offer_accepted": accepted, "outcome": outcome, "confidence": 0.8})

    def _agent(input_list, cid, sub, conn_, rec, system=runtime.SYSTEM, on_step=None):
        if not rec["offers"]:                      # present an offer on the first turn only
            o = offers.authorize(rec["offers"], "pause", {"months": 1})
            offers.present(rec["offers"], o, {"months": 1})
        input_list.append({"role": "assistant", "content": "Here is a pause."})
        return "Here is a pause."
    monkeypatch.setattr(runtime, "_agent_turn", _agent)
    # the simulated customer never answers the offer, then the conversation runs out of turns
    monkeypatch.setattr(runtime.sim, "respond",
                        lambda scenario, msg, hist: {"reply": "hmm, let me think", "decision": "continue"})

    scenario = dict(conn.execute("SELECT * FROM scenarios WHERE kind='churn' LIMIT 1").fetchone())
    batch_rec = runtime.simulate_conversation(scenario, conn)
    batch_states = [o["state"] for o in batch_rec["evidence"]["offers"]]

    monkeypatch.setattr(runtime, "_grade_and_store", lambda *a, **k: None)
    session = runtime.new_session(scenario["customer_id"], conn)
    session["_session_id"] = "parity-live"
    runtime.live_turn(session, "your price is too high", conn)
    live_rec = runtime.resolve_session(session, conn, resolution_key="parity-live")
    live_states = [o["state"] for o in live_rec["evidence"]["offers"]]

    assert batch_states == live_states == ["abandoned"], (batch_states, live_states)
    assert batch_rec["offer_made"] == live_rec["offer_made"] == "1-month pause"
    conn.close()


def test_an_unanswered_offer_is_abandoned_not_rejected():
    """'rejected' asserts a customer DECISION. A turn-limit exit, or a cancellation routed
    while an offer sat on the table, is not the customer declining it — and the judge reads
    that word."""
    from agent import runtime
    rec = runtime._new_rec()
    o = offers.authorize(rec["offers"], "discount", {"pct": 10})
    offers.present(rec["offers"], o, {"pct": 10})
    runtime._close_dangling_offer(rec, "lost")
    assert rec["offers"][0].state == "abandoned"
    assert offers.rejected_of_kind(rec["offers"], "discount") is None, \
        "an unanswered offer must not read as a customer rejection"


def test_an_accepted_offer_is_never_reclassified_by_the_terminal_transition():
    from agent import runtime
    rec = runtime._new_rec()
    o = offers.authorize(rec["offers"], "pause", {"months": 1})
    offers.present(rec["offers"], o, {"months": 1})
    offers.mark_accepted(rec["offers"])
    runtime._close_dangling_offer(rec, "lost")
    assert offers.accepted(rec["offers"]) is not None


def test_an_abandoned_offer_was_still_extended_to_the_customer():
    """R13-CRITICAL, self-inflicted by R12. mark_abandoned added a FOURTH terminal state and
    extended() was never taught about it, so an offer presented and never answered vanished
    from offer_made — and with it from offer-effectiveness analytics, the save cooldown, and
    the judge's evidence. On the committed run that dropped 68 of 160 conversations, every
    one a LOSS, so the exclusion was purely survivorship-directional."""
    from agent import runtime
    rec = runtime._new_rec()
    o = offers.authorize(rec["offers"], "pause", {"months": 3})
    offers.present(rec["offers"], o, {"months": 3})
    runtime._close_dangling_offer(rec, "lost")

    assert rec["offers"][0].state == "abandoned"
    assert offers.extended(rec["offers"]) is not None, "an unanswered offer was still made"
    assert offers.offer_summary(rec["offers"]) == "3-month pause"
    assert runtime._offer_made(rec) == "3-month pause", "the persisted column must record it"


def test_extended_covers_every_terminal_state_the_ledger_can_reach():
    """The bug above was a state added in one place and not the other. Pin the invariant
    rather than the instance: every state a terminal transition can produce must be
    classifiable by extended(), so the next added state fails here instead of silently
    deleting data from the analytics layer."""
    from agent import runtime
    terminal_states = set()
    for outcome, decision in (("lost", None), ("lost", "reject"), ("saved", "accept")):
        rec = runtime._new_rec()
        o = offers.authorize(rec["offers"], "pause", {"months": 1})
        offers.present(rec["offers"], o, {"months": 1})
        if decision:
            runtime._apply_customer_decision(rec, decision)
        else:
            runtime._close_dangling_offer(rec, outcome)
        terminal_states.add(rec["offers"][0].state)
        assert offers.extended(rec["offers"]) is not None, \
            f"state {rec['offers'][0].state!r} is reachable but invisible to extended()"
    assert terminal_states == {"abandoned", "rejected", "accepted"}, terminal_states
    # ...and the INVARIANT, derived rather than hand-listed. The version above pins three
    # INSTANCES, so adding a fifth terminal state the way R12 added the fourth — a new mark_*
    # plus a branch in _close_dangling_offer — left the suite green while extended() returned
    # None and the offer vanished from offer_made, analytics and the judge envelope again.
    # Every state any mark_* helper can produce must be classifiable by extended().
    import inspect as _inspect
    produced = set()
    for name, fn in vars(offers).items():
        if not (name.startswith("mark_") and callable(fn)):
            continue
        for line in _inspect.getsource(fn).splitlines():
            if ".state = " in line and '"' in line:
                produced.add(line.split('"')[1])
    assert produced, "no mark_* helpers found — this invariant check has stopped working"
    for state in produced:
        led = [offers.Offer(id="x", kind="pause", authorized_terms={"months": 1},
                            state=state, presented_terms={"months": 1})]
        assert offers.extended(led) is not None, (
            f"offers.{state!r} is reachable via a mark_* helper but extended() cannot see it — "
            f"this is the exact shape of the R12 CRITICAL: a state added in one place and not "
            f"taught to the accessor every consumer reads")


def test_write_before_promise_works_on_a_pre_r12_database(tmp_path):
    """R13-D8f: the R12 write-before-promise enqueues at TURN time, before the conversation
    row exists, so conversation_id must be NULL initially. Fresh DBs get that from CREATE
    TABLE, but ALTER TABLE ADD COLUMN cannot relax an existing NOT NULL — so on any pre-R12
    database the surviving constraint rejected the pre-write, and INSERT OR IGNORE swallowed
    the rejection. The durable write was a silent no-op while the customer still got 'our
    team will apply it'. The migration added the column; it did not add the durability."""
    import sqlite3
    import db as _db
    from agent import runtime

    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE conversations (id INTEGER PRIMARY KEY);
        CREATE TABLE offer_fulfillment_requests (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id),
            offer TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO conversations (id) VALUES (1);
        INSERT INTO offer_fulfillment_requests (conversation_id, offer, status, created_at)
            VALUES (1, '20% discount', 'pending_application', 't');
    """)
    raw.commit()
    raw.close()

    conn = _db.connect(path)
    _db.init_db(conn)                             # the migration entry point
    runtime._queue_fulfillment_live(conn, "legacy-session", "1-month pause")

    row = conn.execute("SELECT offer, conversation_id FROM offer_fulfillment_requests "
                       "WHERE session_key='legacy-session'").fetchone()
    assert row is not None, "the promise must be durable on a legacy schema too"
    assert row["conversation_id"] is None and row["offer"] == "1-month pause"
    # the pre-existing row survived the table rebuild
    assert conn.execute("SELECT count(*) c FROM offer_fulfillment_requests "
                        "WHERE conversation_id=1").fetchone()["c"] == 1
    conn.close()
