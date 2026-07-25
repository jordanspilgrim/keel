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
    sp = agg["segment_save_pp"]
    treated_n = agg["treated_cohort_n"]
    for rel in ("docs/index.html", "docs/how-it-works.html"):
        text = _read(rel)
        assert str(sp["median"]) in text, f"{rel} must cite the median lift {sp['median']}"
        assert str(sp["max"]) in text, f"{rel} must cite the range max {sp['max']}"
        assert f"n={treated_n}" in text, f"{rel} must cite the treated cohort n={treated_n}"


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
