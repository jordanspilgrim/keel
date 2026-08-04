"""RED step for the claims-gate extension — demonstrate each new assertion FAILING.

A gate that has only ever been seen passing proves nothing; that is the finding this whole
pass rests on. So each of the five new assertions in tests/test_claims.py is shown catching a
deliberately-staled doc, in a throwaway copy of the tree. The working tree is never touched.

    /Users/gabriel/ClaudeCode/keel/.venv/bin/python scripts/validate_claims_gate.py

Run from the repo root. Each scenario runs the FULL suite in its copy — never `-k`, never a
path narrowing, because every one of those makes the run a subset and the assertions skip
themselves. That skip is by design (they reconcile against the collected total, which a subset
does not have) and it is also how a "I ran the test" can be false. Measured, not assumed.

Scenarios report every assertion they trip, not just the intended one. Some staleness trips
more than one, and reporting a clean one-to-one mapping would be tidier than the truth.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEW = {
    "test_operating_notes_state_the_actual_collected_count": "A collected",
    "test_operating_notes_never_state_a_bare_pass_count": "B bare pass count",
    "test_every_declared_pytest_summary_reconciles_to_collected": "C arithmetic",
    "test_every_test_named_in_the_operating_notes_is_real": "D node id exists",
    "test_the_operating_notes_agree_on_which_tests_fail": "E cross-file agreement",
}
PRE_EXISTING = {
    "test_the_probe_covers_a_cue_grid_not_a_single_phrasing",
    "test_the_fairness_gate_checks_orthography_not_just_group",
}

# (label, file, find, replace) — each is a plausible way a doc goes stale.
SCENARIOS = [
    ("A  a collected count left at the previous value",
     "CLAUDE.md", "392 collected", "387 collected"),
    ("B  a bare pass count, the exact shape of `# expect 364 passed`",
     "CLAUDE.md", ".venv/bin/python scripts/mutate.py",
     "# expect 392 passed\n.venv/bin/python scripts/mutate.py"),
    ("C  a summary whose arithmetic no longer reconciles",
     "CLAUDE.md", "`2 failed, 390 passed`", "`2 failed, 385 passed`"),
    ("D  a node id left pointing at a renamed test",
     ".claude/project.md",
     "tests/test_guardrails.py::test_the_fairness_gate_checks_orthography_not_just_group",
     "tests/test_guardrails.py::test_this_was_renamed_three_commits_ago"),
    ("E  one surface updated, the other not — how staleness actually arrives",
     ".claude/project.md",
     "  `tests/test_agent_fairness.py::test_the_probe_covers_a_cue_grid_not_a_single_phrasing` and\n",
     ""),
]


def run(tree: str) -> tuple[set[str], dict[str, str]]:
    """(failed test names, {test name: its own assertion message}).

    The message MUST come from the named test's own FAILURES block. A first version grabbed
    the first `AssertionError:` anywhere in stdout, which is the pre-existing fairness
    failure — so every scenario printed the same unrelated message as if it were the
    assertion's evidence. That is the defect these tests exist to catch, in the harness
    demonstrating them."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
                           "-p", "no:randomly"],
                          cwd=tree, capture_output=True, text=True)
    failed = {ln[len("FAILED "):].split(" - ")[0].split("::")[-1].strip()
              for ln in proc.stdout.splitlines() if ln.startswith("FAILED ")}
    messages: dict[str, str] = {}
    current = None
    for ln in proc.stdout.splitlines():
        header = re.match(r"^_{3,}\s+(\S+)\s+_{3,}$", ln)
        if header:
            current = header.group(1)
            continue
        if current and current not in messages and ln.startswith("E "):
            body = ln[2:].strip()
            if body.startswith("AssertionError:"):
                body = body[len("AssertionError:"):].strip()
            if body:
                messages[current] = body
    return failed, messages


def copy_tree(tmp: str) -> str:
    tree = os.path.join(tmp, "tree")
    shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", "*.pyc", ".pytest_cache"))
    return tree


print("=" * 78)
print("BASELINE — the docs are correct, so no NEW assertion may fire")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="keel-claims-red-")
failed, _msgs = run(copy_tree(tmp))
fired = sorted(NEW[f] for f in failed & set(NEW))
print(f"  failed: {sorted(failed)}")
print(f"  new assertions fired: {fired or 'NONE'}   <- must be NONE")
print(f"  pre-existing fairness failures present: {failed >= PRE_EXISTING}   <- must be True")
shutil.rmtree(tmp, ignore_errors=True)
ok = not fired and failed >= PRE_EXISTING

for label, rel, find, repl in SCENARIOS:
    print()
    print("=" * 78)
    print(f"RED  {label}")
    print("=" * 78)
    tmp = tempfile.mkdtemp(prefix="keel-claims-red-")
    tree = copy_tree(tmp)
    path = os.path.join(tree, rel)
    src = open(path, encoding="utf-8").read()
    if src.count(find) != 1:
        print(f"  ANCHOR MISS in {rel}: {src.count(find)} matches for {find[:60]!r}")
        shutil.rmtree(tmp, ignore_errors=True)
        ok = False
        continue
    open(path, "w", encoding="utf-8").write(src.replace(find, repl))
    print(f"  staled {rel}: {find[:64]!r} -> {repl[:64]!r}")
    failed, messages = run(tree)
    fired = sorted(NEW[f] for f in failed & set(NEW))
    print(f"  new assertions fired: {fired or 'NONE — THE GATE DID NOT CATCH IT'}")
    for name in sorted(failed & set(NEW)):
        msg = re.sub(r"\s+", " ", messages.get(name, "(no message captured)"))
        print(f"    {NEW[name]}: {msg[:180]}")
    shutil.rmtree(tmp, ignore_errors=True)
    if not fired:
        ok = False

print()
print("=" * 78)
print(f"RESULT: {'every scenario was caught' if ok else 'AT LEAST ONE SCENARIO WAS MISSED'}")
print("=" * 78)
sys.exit(0 if ok else 1)
