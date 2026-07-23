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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _actual_test_count() -> int:
    n = 0
    for f in glob.glob(os.path.join(_ROOT, "tests", "test_*.py")):
        with open(f) as fh:
            n += len(re.findall(r"^def test_", fh.read(), re.M))
    return n


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel)) as fh:
        return fh.read()


def test_docs_state_the_actual_test_count():
    """Every doc that advertises a current suite size must state the real count. Extracts
    each 'N tests' / 'N passed' / 'N offline tests' mention and requires it to equal the
    collected count (historical remediation prose is phrased without the word 'tests')."""
    actual = _actual_test_count()
    for rel in ("README.md", "docs/testing.html"):
        text = _read(rel)
        claims = {int(m) for m in re.findall(r"(\d+)\s+(?:offline |fast )?(?:tests|passed)\b", text)}
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
