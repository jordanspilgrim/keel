"""RED step for the changed-failing-assert refinement.

THE HOLE. The oracle compares node ids: a kill requires a failure the baseline did not have.
So a mutant whose ONLY guarding test is already failing at baseline reports SURVIVED whether
or not the control is guarded — the id is unchanged, and that the test now fails for a
completely different reason is invisible. Measured live on the pre-Phase-1 tree:
`proxy_probe_single_cue` failed at `assert v["leaked_cells"] == 0` at baseline and at
`assert len(af._PROBE_CUES) >= 4` under mutation, same node id, guard firing, SURVIVED.

A false SURVIVED is the safe direction — it understates coverage and can never certify a
control — but it still reads as "no test kills this" when the truth is "its test is busy
failing at something else."

THE RED STEP IS CONSTRUCTED, and deliberately so. `main` is at 30/30 with no naturally
masked mutant left, and waiting for one would make the refinement unfalsifiable by
demonstration. Constructing the antecedent of a conditional is a unit test, not fabrication —
the F4c-conditional ruling, which by now has done work in four separate items. So this builds
a throwaway tree containing a guard followed by a deliberately-failing assert, and a mutation
that trips the guard: exactly the shape, with the answer known in advance.

    /Users/gabriel/ClaudeCode/keel/.venv/bin/python scripts/validate_changed_reason.py

Reads only; the working tree is never touched. No network, no billed calls.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GUARD_MODULE = '''"""Scratch fixture for the changed-reason proof. Not part of the product."""
GUARD_CUES = ["a", "b", "c", "d"]
'''

# A guard assert, then a residual that fails whatever the guard does. The baseline therefore
# fails at the RESIDUAL; under the mutation it fails at the GUARD — same node id, different
# assert. That is the entire property, isolated.
GUARD_TEST = '''from scratch_guard import GUARD_CUES


def test_guard_then_residual():
    assert len(GUARD_CUES) >= 4, "the guard"
    assert False, "a deliberate residual, failing at baseline whatever the guard does"
'''


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = ROOT
    return mod


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


mut = load(os.path.join(ROOT, "scripts", "mutate.py"), "repaired")
tmp = tempfile.mkdtemp(prefix="keel-changed-reason-")
tree = os.path.join(tmp, "tree")
shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "*.pyc", "keel.db*", ".pytest_cache"))
open(os.path.join(tree, "scratch_guard.py"), "w").write(GUARD_MODULE)
open(os.path.join(tree, "tests", "test_zz_scratch_guard.py"), "w").write(GUARD_TEST)

mut.ROOT = tree                      # run the harness against the constructed tree


def run_state(mutated: bool):
    p = os.path.join(tree, "scratch_guard.py")
    open(p, "w").write(GUARD_MODULE.replace('["a", "b", "c", "d"]', '["a"]')
                       if mutated else GUARD_MODULE)
    proc = mut._pytest(tree)
    return (set(mut._failing_tests(proc.stdout)),
            mut._failure_asserts(proc.stdout),
            proc.returncode)


banner("A. THE BASELINE — the scratch test fails at the RESIDUAL")
base_failing, base_asserts, rc = run_state(mutated=False)
node = next(n for n in base_failing if "scratch_guard" in n)
print(f"  exit={rc}   failing: {sorted(n.split('::')[-1] for n in base_failing)}")
print(f"  its failing assert: {base_asserts['test_guard_then_residual']}")

banner("B. UNDER MUTATION — same node id, DIFFERENT assert")
mut_failing, mut_asserts, rc = run_state(mutated=True)
print(f"  exit={rc}   failing: {sorted(n.split('::')[-1] for n in mut_failing)}")
print(f"  its failing assert: {mut_asserts['test_guard_then_residual']}")
print(f"\n  node id identical?      {node in mut_failing}")
print(f"  NEW failures (old oracle's only signal): "
      f"{sorted(mut_failing - base_failing) or 'NONE'}")

banner("C. THE TWO ORACLES ON THAT SAME RUN")
old_killed = rc == 1 and bool(mut_failing - base_failing)
changed = mut._changed_reason(mut_failing, base_failing, mut_asserts, base_asserts)
new_killed = rc == 1 and bool((mut_failing - base_failing) or changed)
print(f"  pre-refinement  killed = {old_killed}    <- the false SURVIVED")
print(f"  post-refinement killed = {new_killed}    <- must be True")
print(f"  changed-reason set: {sorted(n.split('::')[-1] for n in changed)}")

banner("D. IT MUST NOT FIRE WHEN THE REASON IS UNCHANGED")
same = mut._changed_reason(base_failing, base_failing, base_asserts, base_asserts)
print(f"  baseline compared with itself -> {sorted(same) or 'EMPTY'}   <- must be empty")

shutil.rmtree(tmp, ignore_errors=True)
ok = old_killed is False and new_killed is True and not same
banner(f"RESULT: {'the refinement closes the hole and does not over-fire' if ok else 'FAILED'}")
sys.exit(0 if ok else 1)
