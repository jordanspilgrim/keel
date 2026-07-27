"""Claims-consistency guard (L2): the numbers the public docs advertise must match the
canonical sources — the actual test suite and the committed run manifest — so the repo
can't drift into stating different generations of the system across README/BUILD/docs.
No network.
"""

from __future__ import annotations

import glob
import json
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _declared_test_functions() -> int:
    """How many `def test_` functions exist. NOT the number a user sees: a parametrized
    function is one def but N collected tests."""
    n = 0
    for f in glob.glob(os.path.join(_ROOT, "tests", "test_*.py")):
        with open(f) as fh:
            n += len(re.findall(r"^def test_", fh.read(), re.M))
    return n


def _read_dashboard_data() -> dict:
    """The committed dashboard payload.

    Reads data.js, which is TRACKED, rather than data.json, which .gitignore excludes. The
    two are byte-identical apart from the `window.KEEL_DATA = ` prefix, but only one of them
    exists in a fresh clone — so these guards (the R10-F1 median-run check and the
    eval-population check) failed with FileNotFoundError on `pytest tests/`, the very first
    command the README lists under "no API key needed". Verified against a clean clone of
    HEAD: 2 failed, before this."""
    raw = _read("dashboard/data.js")
    return json.loads(raw.split("=", 1)[1].strip().rstrip(";\n").rstrip(";"))


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel)) as fh:
        return fh.read()


def test_docs_state_the_actual_test_count(request):
    """Every doc that advertises a current suite size must state the real count. Extracts
    each 'N tests' / 'N passed' / 'N offline tests' mention and requires it to equal the
    collected count (historical remediation prose is phrased without the word 'tests').

    The count comes from pytest's OWN collection, not a `def test_` grep. The grep
    undercounts a parametrized function -- one def, N collected tests -- so the docs would
    claim a smaller number than the "N passed" a reader sees when they run the suite.
    Skipped on a subset run, where the collected count is legitimately smaller."""
    actual = len(request.session.items)
    if actual < _declared_test_functions():
        pytest.skip("subset run — the collected count is not the full suite")
    for rel in ("README.md", "docs/testing.html"):
        text = _read(rel)
        # a number then an OPTIONAL comma-separated adjective list then tests/passed, so
        # "152 fast, deterministic tests" is caught as well as "152 tests" / "152 passed" —
        # but NOT a stray "0 and the unit tests" (connectives aren't a comma-list).
        claims = {int(m) for m in re.findall(
            r"(\d+)\s+(?:[A-Za-z]+(?:,\s+[A-Za-z]+)*\s+)?(?:tests|passed)\b", text)}
        assert claims, f"{rel} states no test count"
        assert claims == {actual}, f"{rel} claims {claims}, actual is {actual}"


def test_compliance_metric_not_described_as_audit_coverage():
    """M2: the canonical compliance_coverage checks TRANSCRIPT disclosure only — no
    public surface may describe it as covering audit records (which it never queries)."""
    for rel in ("console/index.html", "docs/keel-dashboard.html", "dashboard/index.html", "BUILD.md"):
        try:
            text = _read(rel).lower()
        except FileNotFoundError:
            continue
        assert "disclosure + audit" not in text, f"{rel} overclaims audit coverage"
        assert "present and an audit record" not in text, f"{rel} overclaims audit coverage"


def test_committed_manifest_is_the_pre_registered_median_run():
    """The demo headline is a pre-registered median-of-k, so dashboard/manifest.json must be
    the MEDIAN-lift run drawn from the same k-run set recorded in demo_aggregate.json — not
    a hand-picked or stray run. Pure JSON↔JSON provenance, independent of any doc prose."""
    agg = json.loads(_read("dashboard/demo_aggregate.json"))
    man = json.loads(_read("dashboard/manifest.json"))
    assert man["run_id"] == agg["committed_run_id"], "manifest is not the aggregate's committed run"
    assert man["run_id"] in agg["run_ids"] and len(agg["run_ids"]) == agg["k"]
    sp = agg["segment_save_pp"]
    assert man["lift"]["segment_save_pp"] == sp["median"], "committed manifest's lift is not the median"
    assert sp["min"] == min(sp["values"]) and sp["max"] == max(sp["values"])
    assert len(sp["values"]) == agg["k"], "the aggregate must retain every one of the k draws"


def test_readme_cites_the_pre_registered_median_and_range():
    """README must cite the median lift, the range endpoints, the method, and the committed
    median run — all extracted from demo_aggregate.json / manifest.json so they stay true
    across re-runs (no hand-typed numbers to drift)."""
    agg = json.loads(_read("dashboard/demo_aggregate.json"))
    man = json.loads(_read("dashboard/manifest.json"))
    readme = _read("README.md")
    sp = agg["segment_save_pp"]
    assert man["run_id"] in readme, "README does not cite the committed run_id"
    assert str(sp["median"]) in readme, f"README must cite the median lift {sp['median']}"
    assert str(sp["max"]) in readme, f"README must cite the range max {sp['max']}"
    assert ("median of k=5" in readme.lower() or "median-of-5" in readme.lower()
            or "median of 5" in readme.lower()), "README must name the median-of-k method"
    # the concrete committed median run's before/after is also cited
    sb, sa = round(man["baseline"]["segment_save_rate"] * 100), round(man["after"]["segment_save_rate"] * 100)
    assert f"{sb}%" in readme and f"{sa}%" in readme, f"README must cite the median run's {sb}% -> {sa}%"


def test_readme_cites_each_eval_population_with_its_own_number():
    """The dashboard's eval coverage/pass span BOTH arms; the manifest's are AFTER-arm only.
    They legitimately differ, so the README must cite each with its own value rather than
    quoting one number as if it covered both (which previously made 'coverage ≥99%' false
    against the committed manifest)."""
    man = json.loads(_read("dashboard/manifest.json"))
    data = _read_dashboard_data()
    readme = _read("README.md")
    for value in (round(man["after"]["eval_coverage"] * 100, 1),
                  round(man["after"]["eval_pass_rate"] * 100, 1),
                  round(data["kpis"]["eval_coverage"] * 100, 1),
                  round(data["kpis"]["eval_pass_rate"] * 100, 1)):
        assert f"{value}%" in readme, f"README must cite {value}% (its population's own figure)"
    # and the committed run's OWN margin-adjusted lift, not only the aggregate median
    assert f"+{man['lift']['segment_madj_pp']}pp" in readme


def test_dashboard_data_renders_the_committed_median_run():
    """R10-F1: the dashboard (window.KEEL_DATA / data.json) is the flagship visible surface —
    it must render the SAME committed (median) run as the manifest, not whatever ran LAST in
    the k-loop. run_median re-exports data.js from the committed run to guarantee this."""
    man = json.loads(_read("dashboard/manifest.json"))
    data = _read_dashboard_data()
    assert data["meta"]["provenance"]["run_id"] == man["run_id"], \
        "dashboard renders a different run than the committed manifest — the primary surface overstates the headline"
    assert round(data["kpis"]["save_delta_pp"], 1) == man["lift"]["segment_save_pp"], \
        "dashboard headline lift != committed manifest lift"


def test_html_demo_docs_cite_the_pre_registered_median():
    """M6: the marketing HTML must cite the SAME pre-registered median + range as
    README/aggregate — no stale cross-run mixing (the sixth-review defect was an old n=20
    cohort beside a freshly-updated lift in one sentence). Extracted from the aggregate."""
    agg = json.loads(_read("dashboard/demo_aggregate.json"))
    man = json.loads(_read("dashboard/manifest.json"))
    sp = agg["segment_save_pp"]
    treated_n = agg["treated_cohort_n"]
    # docs/testing.html was NOT in this tuple, and that is exactly how it kept publishing the
    # superseded +23.3pp batch — wrong median, wrong range, wrong run id, wrong eval rate — as
    # "the pre-registered median-of-5", while README and the other two HTML docs had been
    # reconciled. README links it as one of three browsable docs. Any doc that prints demo
    # numbers belongs in this list.
    for rel in ("docs/index.html", "docs/how-it-works.html", "docs/testing.html"):
        text = _read(rel)
        assert str(sp["median"]) in text, f"{rel} must cite the median lift {sp['median']}"
        assert str(sp["max"]) in text, f"{rel} must cite the range max {sp['max']}"
        assert f"n={treated_n}" in text, f"{rel} must cite the treated cohort n={treated_n}"
        # The run_id was NOT pinned here, and that is exactly how docs/index.html kept
        # naming the PREVIOUS batch's run as "the committed median-lift run, shown on the
        # dashboard" after a re-run had already reconciled the medians around it. Citing
        # the right number beside the wrong provenance is its own kind of false.
        assert man["run_id"] in text, f"{rel} must cite the committed run_id {man['run_id']}"


def test_every_test_cited_in_the_docs_actually_exists():
    """E2E#21: cross-referencing every `test_*` identifier named in BUILD.md/README.md
    against the suite found NINE citations naming tests that exist nowhere in the repo —
    removed by a later refactor, while the rows kept citing them as live evidence. The
    remediation matrix is the document a reviewer uses to check that a claimed fix is
    actually tested, so a citation that resolves to nothing is worse than no citation.
    test_claims only ever checked numeric drift."""
    real = set()
    for f in glob.glob(os.path.join(_ROOT, "tests", "test_*.py")):
        with open(f) as fh:
            real |= set(re.findall(r"^def (test_\w+)", fh.read(), re.M))
    filenames = {os.path.basename(f)[:-3] for f in glob.glob(os.path.join(_ROOT, "tests", "test_*.py"))}
    cited = set()
    for rel in ("BUILD.md", "README.md"):
        cited |= set(re.findall(r"`(test_\w+)`", _read(rel)))
    phantom = sorted(cited - real - filenames)
    assert not phantom, f"docs cite tests that do not exist: {phantom}"


def test_build_md_cites_the_current_median_and_committed_run():
    """BUILD.md is the 'source of truth' doc and was the ONE doc whose demo numbers no test
    pinned — so after the R12 re-run it still carried the previous build's +23.3pp headline in
    two summary rows while README and the HTML docs had been reconciled. Historical rows are
    allowed to name old figures (the estimator's own history is part of the record); what must
    be true is that the CURRENT median, range and committed run_id all appear."""
    agg = json.loads(_read("dashboard/demo_aggregate.json"))
    man = json.loads(_read("dashboard/manifest.json"))
    build = _read("BUILD.md")
    sp = agg["segment_save_pp"]
    assert man["run_id"] in build, "BUILD.md does not cite the committed run_id"
    for value in (sp["median"], sp["min"], sp["max"]):
        assert str(value) in build, f"BUILD.md must cite {value} from the current aggregate"


def test_a_published_catch_rate_is_bound_to_the_guardrail_version_that_produced_it():
    """R14-H1. The kill switch gates on `program_health.version == guardrails.guardrail_version()`,
    but nothing bound the PUBLISHED catch rate to that check — so R13's guardrail edits moved the
    hash and four public surfaces went on advertising "100% (14/14)" as current, with no staleness
    note anywhere (`grep "re-run the red-team"` over the docs returned nothing).

    Any surface printing a catch rate must either be measured under the CURRENT hash or say it is
    superseded. This test enforces the second half; the first half is what re-running the demo does."""
    from agent import guardrails
    current = guardrails.guardrail_version()
    recorded = json.loads(_read("dashboard/manifest.json")).get("guardrail_version")
    if recorded == current:
        return  # measured under the live hash — nothing to disclose
    for rel in ("README.md", "docs/testing.html"):
        text = _read(rel)
        assert "supersede" in text.lower(), (
            f"{rel} publishes a catch rate measured under {recorded!r} while the current "
            f"guardrail version is {current!r}, and says nothing about it being stale")


def test_no_doc_quotes_a_guardrail_hash_as_current():
    """R15. BUILD.md quoted the live guardrail hash as "current"; the very next guardrail edit
    moved it, so the disclosure paragraph about a stale hash was itself quoting a stale hash.
    Hard-coding a live content hash in prose guarantees that recurrence — the only stable form
    is a command the reader runs. A RECORDED hash (a fact about a past measurement) is fine and
    is what this allows: the current one must never be pinned."""
    from agent import guardrails
    current = guardrails.guardrail_version()
    for rel in ("README.md", "BUILD.md", "docs/testing.html", "docs/index.html",
                "docs/how-it-works.html"):
        try:
            text = _read(rel)
        except FileNotFoundError:
            continue
        assert current not in text, (
            f"{rel} hard-codes the CURRENT guardrail hash {current!r}. It will be wrong the "
            f"next time a pattern, flag, replacement or classifier prompt changes. Quote the "
            f"recorded hash and the command instead.")


# --- R16 M5: the manifest's Learn->Act lineage must actually dereference ------------
# manifest.json carries the consumed signal TWICE: inline as `intervention_signal`, and by
# reference as `intervention_signal_id`. The repair script's re-export recomputed the inline
# copy and left the id alone, so the committed manifest CONTRADICTED ITSELF -- inline said
# pause 0.205/n=44, following the id gave 0.36/n=25. Nothing detected it because nothing had
# ever checked the two against each other.


def _committed_conn():
    """Connect to the COMMITTED artifact DB, or skip.

    `keel.db` is deliberately untracked — it is a run artifact, not source. A fresh clone,
    CI, and the mutation harness's copied tree all lack it, and an artifact-consistency test
    that explodes there is a broken test, not a finding. (It also silently turned the
    mutation baseline red, which aborts the whole harness.) The LOGIC these guard is covered
    portably by the temp-DB tests below; this pair checks the SHIPPED artifact.
    """
    import db
    conn = db.connect()
    have = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'").fetchone()
    if have is None:
        pytest.skip("committed keel.db not present (untracked artifact) — logic covered by "
                    "the temp-DB tests")
    return conn

def _canonical_manifest() -> dict:
    with open(os.path.join(_ROOT, "dashboard", "manifest.json")) as fh:
        return json.load(fh)


def test_the_committed_manifest_signal_id_resolves_to_its_inline_signal():
    """The id and the inline blob are two representations of ONE fact. If they can disagree,
    the cheaper-to-read one silently wins and the lineage claim is decorative."""
    from analytics import themes

    man = _canonical_manifest()
    conn = _committed_conn()
    consumed = themes.resolve_signal_for_run(
        conn, man["intervention_signal_id"], man["run_id"])
    assert consumed is not None, (
        f"intervention_signal_id={man['intervention_signal_id']} does not resolve to a "
        f"signal belonging to run {man['run_id']!r}")
    assert man["intervention_signal"] == consumed, (
        "manifest.intervention_signal disagrees with the row its own id points at")


def test_the_post_repair_recomputation_is_labelled_not_substituted():
    """`intervention_signal` is the record of what Act CONSUMED. Overwriting it with a
    recomputation rewrites history to look better than the run was; the corrected numbers
    belong beside it, marked as something Act never saw."""
    man = _canonical_manifest()
    corr = man.get("intervention_signal_recomputed_post_repair")
    assert corr, "the post-repair recomputation was dropped rather than published"
    assert "NEVER saw" in corr["note"]
    consumed_pause = [o for o in man["intervention_signal"]["offer_effectiveness"]
                      if o["offer"] == "pause"][0]
    recomputed_pause = [o for o in corr["signal"]["offer_effectiveness"]
                        if o["offer"] == "pause"][0]
    # The repair enlarges denominators with failures, so the corrected view must be WORSE.
    assert recomputed_pause["save_rate"] < consumed_pause["save_rate"]
    assert recomputed_pause["n"] > consumed_pause["n"]


def test_a_signal_id_cannot_be_dereferenced_across_runs():
    """Signal ids are only meaningful against the database that produced them, and
    `--median` retains only the committed run's. Without the run_id check a stale id lands
    on an unrelated row and answers plausibly instead of failing -- which is how 13 of 14
    retained manifests came to cite a signal that is not theirs."""
    from analytics import themes

    man = _canonical_manifest()
    conn = _committed_conn()
    sid = man["intervention_signal_id"]
    assert themes.resolve_signal_for_run(conn, sid, "run-does-not-exist") is None
    assert themes.resolve_signal_for_run(conn, sid, man["run_id"]) is not None


def test_load_signal_returns_none_on_an_ephemeral_row_instead_of_raising():
    """`signals` holds structured JSON experiment signals AND plain-sentence ephemeral theme
    rankings. json.loads ran unconditionally, so any manifest citing an ephemeral id raised
    JSONDecodeError out of a `dict | None` API."""
    from analytics import themes

    conn = _committed_conn()
    ephemeral = conn.execute(
        "SELECT id FROM signals WHERE run_id IS NULL LIMIT 1").fetchone()
    if ephemeral is None:
        pytest.skip("no ephemeral signal rows in the committed DB")
    assert themes.load_signal(conn, ephemeral["id"]) is None


# --- the same two controls, portably: a temp DB, so a fresh clone / CI / the mutation
# harness's copied tree all exercise them. The artifact tests above check the SHIPPED
# manifest; these check the CODE, and are what the mutation catalogue can anchor on.


def _temp_signals_db(tmp_path):
    import db
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_db(conn)
    return conn


def test_load_signal_tolerates_the_ephemeral_row_shape(tmp_path):
    """`signals` holds structured JSON experiment signals AND plain-sentence ephemeral theme
    rankings (see themes.persist). json.loads ran unconditionally, so any id in the ephemeral
    space raised JSONDecodeError out of a documented `dict | None`."""
    from analytics import themes

    conn = _temp_signals_db(tmp_path)
    tid = conn.execute(
        "INSERT INTO themes (label, summary, size, save_rate, avg_margin_cost, "
        "example_ids_json, created_at) VALUES (?,?,?,?,?,?,?)",
        ("Price sensitivity", "s", 54, 0.11, 0.0, "[]", "t")).lastrowid
    conn.execute("INSERT INTO signals (theme_id, recommendation, priority_score, created_at) "
                 "VALUES (?,?,?,?)", (tid, "'Price sensitivity' drives 54 conversations.", 1.0, "t"))
    conn.commit()
    eid = conn.execute("SELECT id FROM signals").fetchone()["id"]
    assert themes.load_signal(conn, eid) is None      # not a signal — must not raise
    assert themes.load_signal(conn, 99999) is None    # missing — same answer


def test_resolve_signal_for_run_refuses_a_cross_run_dereference(tmp_path):
    """Signal ids only mean anything against the database that produced them, and
    `--median` retains just the committed run's. Without the run_id check a stale id lands on
    an unrelated row and answers PLAUSIBLY rather than failing — which is how 13 of the 14
    retained manifests came to cite a signal that is not theirs."""
    from analytics import themes

    conn = _temp_signals_db(tmp_path)
    sid = themes.persist_signal(conn, {"segment": "Price too high"}, run_id="run-A")
    assert themes.resolve_signal_for_run(conn, sid, "run-A") == {"segment": "Price too high"}
    assert themes.resolve_signal_for_run(conn, sid, "run-B") is None, \
        "an id from another run resolved instead of failing"
    assert themes.resolve_signal_for_run(conn, sid, None) is None
