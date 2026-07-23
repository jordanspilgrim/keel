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


def test_readme_matches_the_committed_run_manifest():
    """The README's committed-run numbers must match dashboard/manifest.json exactly."""
    m = json.loads(_read("dashboard/manifest.json"))
    readme = _read("README.md")
    assert m["run_id"] in readme, "README does not cite the committed run_id"
    seg_before = round(m["baseline"]["segment_save_rate"] * 100)
    seg_after = round(m["after"]["segment_save_rate"] * 100)
    assert f"{seg_before}%" in readme and f"{seg_after}%" in readme, \
        f"README must cite the committed segment lift {seg_before}% -> {seg_after}%"
    eval_pct = round(m["after"]["eval_pass_rate"] * 100)
    assert f"{eval_pct}%" in readme, f"README must cite the committed eval pass rate {eval_pct}%"
