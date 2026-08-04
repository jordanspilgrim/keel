"""RED step for the kill-oracle repair — demonstrate the defect, then the repair, both ways.

THE DEFECT. `mutate.py`'s kill oracle was `returncode == 1 and bool(failing)`, guarded by a
requirement that the baseline be GREEN. That guard was a proxy: it delivered attribution by
making "already broken" impossible rather than by checking it. Phase 0 left `pytest`
deliberately RED on two fairness tests — a repaired gate must be seen failing before the defect
under it is fixed — and at that point the proxy inverts. Every mutant satisfies `returncode == 1`
and `bool(failing)` from the PRE-EXISTING failures alone.

THE INSTRUMENT. A NO-OP mutant: it edits a COMMENT in `economics.py`. It changes zero
behaviour, so a correct oracle must report it SURVIVED. Anything else is the oracle reading the
pre-existing red. This is the mirror image of what the harness is for — instead of asking
"does a real change break a test?", it asks "does no change at all get credited with breaking
one?"

    /Users/gabriel/ClaudeCode/keel/.venv/bin/python scripts/validate_kill_oracle.py

Run from the repo root. Section A loads the PRE-REPAIR mutate.py from the merge-base with
origin/main (override with argv[1]) — NOT from HEAD, which after this lands would compare the
repaired oracle with itself and prove nothing.

No billed calls; nothing here runs the agent or run_demo.py. The working tree is never touched.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NOOP_ANCHOR = "Pure arithmetic, no API."
NOOP = ("noop_comment_edit", "economics.py", NOOP_ANCHOR, NOOP_ANCHOR + " NO-OP EDIT.",
        "NOTHING — this mutation changes no behaviour whatsoever")
REAL_MUTANTS_TO_SPOT_CHECK = 2


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_pre_repair(ref: str, tmp: str):
    src = subprocess.run(["git", "show", f"{ref}:scripts/mutate.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    path = os.path.join(tmp, "pre_repair_mutate.py")
    open(path, "w", encoding="utf-8").write(src)
    mod = load(path, "pre_repair_mutate")
    if not hasattr(mod, "_baseline_is_green"):
        sys.exit(f"{ref} does not hold the PRE-REPAIR mutate.py (no _baseline_is_green). "
                 f"Pass the pre-repair ref as argv[1].")
    # mutate.py computes ROOT from its own __file__. Loaded out of a temp dir, that ROOT is the
    # temp dir, and copytree(ROOT, ROOT/tree) recurses into itself — 39 MB before it errored,
    # measured. Point it back at the real repo. (validate_completeness_gate.py loads the same
    # way and is unaffected only because the function it calls never touches ROOT.)
    mod.ROOT = ROOT
    return mod


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


import tempfile  # noqa: E402

tmp = tempfile.mkdtemp(prefix="keel-oracle-validate-")
REF = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
    ["git", "merge-base", "HEAD", "origin/main"], cwd=ROOT,
    capture_output=True, text=True, check=True).stdout.strip()
old = load_pre_repair(REF, tmp)
new = load(os.path.join(ROOT, "scripts", "mutate.py"), "repaired_mutate")
print(f"pre-repair ref: {REF}")

ok = True

banner("A. THE BASELINE — what the two oracles are working against")
usable, base_failing, detail = new._baseline()
print(f"  repaired _baseline(): usable={usable}")
print(f"  detail  : {detail}")
print(f"  failing : {sorted(t.split('::')[-1] for t in base_failing) or 'NONE'}")
old_ok, old_detail = old._baseline_is_green()
print(f"\n  pre-repair _baseline_is_green() -> {old_ok}   ({old_detail})")
print("  => the OLD harness ABORTS here and runs no mutant at all. That abort is the only")
print("     thing holding back section B.")

banner("B. THE DEFECT — the pre-repair oracle, with its abort bypassed")
name, killed_old, d_old = old._run(NOOP, keep=False)
print(f"  NO-OP mutant (edits a COMMENT, changes nothing)")
print(f"  KILLED? {killed_old}    <- the defect: a no-op is credited with a kill")
print(f"  detail: {d_old}")
if not killed_old:
    print("  !! expected the pre-repair oracle to report KILLED here")
    ok = False

banner("C. THE REPAIR — the same no-op, same tree, repaired oracle")
name, killed_new, d_new = new._run(NOOP, keep=False, baseline_failing=base_failing)
print(f"  KILLED? {killed_new}    <- must be False")
print(f"  detail: {d_new}")
if killed_new:
    print("  !! THE REPAIR DID NOT HOLD")
    ok = False

banner("D. THE OTHER DIRECTION — real mutants must STILL be killed")
for m in new.MUTANTS[:REAL_MUTANTS_TO_SPOT_CHECK]:
    n, k, d = new._run(m, keep=False, baseline_failing=base_failing)
    print(f"  {'KILLED  ' if k else 'SURVIVED'}  {n:34} {d[:120]}")
    if not k:
        print("  !! a real mutant stopped being killed — the repair over-corrected")
        ok = False

banner("E. BACKWARD COMPATIBILITY — on a GREEN baseline the repair is a no-op")
n, k, d = new._run(NOOP, keep=False, baseline_failing=frozenset())
print("  repaired oracle, baseline_failing = empty (i.e. what a green tree gives it):")
print(f"  NO-OP KILLED? {k}   <- True, identical to the pre-repair oracle in section B")
print("  So the change alters nothing on a green tree; it only stops the pre-existing")
print("  failures being credited when the tree is red. That is why this is a repair and")
print("  not a bypass — it is strictly stronger, never weaker.")
if not k:
    print("  !! equivalence broken")
    ok = False

import shutil  # noqa: E402
shutil.rmtree(tmp, ignore_errors=True)

banner(f"RESULT: {'the repair holds in both directions' if ok else 'SOMETHING DID NOT HOLD'}")
sys.exit(0 if ok else 1)
