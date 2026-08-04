"""Phase 0.3 acceptance artifact — validate the repaired completeness check against the
KNOWN-BROKEN tree (R17 M22), rather than trusting it because it is new.

Three instruments in pass 18 reproduced the defect they were built to detect, and all three
were caught only by pointing them at a case whose answer was already known. So this runs the
OLD check and the REPAIRED check side by side on (a) the current tree and (b) a tree with
M22's four safety controls physically deleted, and then attacks the repaired check with its
OWN vacuity modes (an empty register, and a register that has collapsed back onto the mutant
table). It reads only; it never runs a mutant, never touches the working tree, and never runs
run_demo.py.

    /Users/gabriel/ClaudeCode/keel/.venv/bin/python scripts/validate_completeness_gate.py

Run from the repo root. Section A loads the pre-repair mutate.py from the merge-base with origin/main (override
with argv[1]) — NOT from HEAD, which after the repair lands would compare the new check
with itself and quietly prove nothing.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.getcwd()
sys.path.insert(0, ROOT)
from scripts import mutate as new  # noqa: E402


def load_pre_repair_mutate(tmp, ref):
    """Load the PRE-REPAIR mutate.py so its check can be run beside the repaired one.

    Deliberately NOT `HEAD`: once the gate repair is committed, HEAD holds the repaired file
    and this proof would silently compare the new check with itself. `ref` defaults to the
    merge-base with origin/main — the tree both checks are being judged against.
    """
    src = subprocess.run(["git", "show", f"{ref}:scripts/mutate.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    path = os.path.join(tmp, "pre_repair_mutate.py")
    open(path, "w", encoding="utf-8").write(src)
    spec = importlib.util.spec_from_file_location("pre_repair_mutate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "_catalogue_is_complete"):
        sys.exit(f"{ref} does not hold the PRE-REPAIR mutate.py (no _catalogue_is_complete). "
                 f"Pass the pre-repair ref as argv[1].")
    return mod


GUTS = [
    ("agent/safety.py",
     '        if pass_rate < config.EVAL_PASS_RATE_FLOOR:\n'
     '            reasons.append(f"eval pass rate {pass_rate:.0%} below floor '
     '{config.EVAL_PASS_RATE_FLOOR:.0%}")\n',
     "        pass  # EVAL PASS-RATE FLOOR DELETED ENTIRELY\n"),
    ("agent/safety.py",
     '        if coverage < _COVERAGE_FLOOR:\n'
     '            reasons.append(f"eval coverage {coverage:.0%} below {_COVERAGE_FLOOR:.0%}")\n',
     "        pass  # EVAL COVERAGE FLOOR DELETED ENTIRELY\n"),
    ("agent/safety.py",
     '        if age is not None and age > config.GUARDRAIL_HEALTH_MAX_AGE_DAYS:\n',
     "        if False:  # GUARDRAIL-HEALTH FRESHNESS GATE DELETED ENTIRELY\n"),
]


def gutted_tree(tmp):
    tree = os.path.join(tmp, "gutted")
    shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", "*.pyc", "keel.db*", ".pytest_cache"))
    for rel, find, repl in GUTS:
        p = os.path.join(tree, rel)
        src = open(p, encoding="utf-8").read()
        assert src.count(find) == 1, f"anchor miss in {rel}: {find[:50]!r}"
        open(p, "w", encoding="utf-8").write(src.replace(find, repl))
    # DISCOUNTS_ENABLED reject block
    p = os.path.join(tree, "agent/policy.py")
    src = open(p, encoding="utf-8").read()
    i = src.index("    if not DISCOUNTS_ENABLED:")
    j = src.index("\n", src.index("\n", i) + 1)
    open(p, "w", encoding="utf-8").write(
        src[:i] + "    if False:  # DISCOUNTS_ENABLED REJECT DELETED ENTIRELY" + src[j:])
    return tree


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


PRE_REPAIR_REF = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
    ["git", "merge-base", "HEAD", "origin/main"], cwd=ROOT,
    capture_output=True, text=True, check=True).stdout.strip()

tmp = tempfile.mkdtemp(prefix="keel-gate-validate-")
head = load_pre_repair_mutate(tmp, PRE_REPAIR_REF)
print(f"pre-repair ref: {PRE_REPAIR_REF}")
tree = gutted_tree(tmp)
print(f"gutted tree: {tree}")
print("deleted: eval pass-rate floor, eval coverage floor, guardrail-health freshness gate, "
      "DISCOUNTS_ENABLED reject")

banner(f"A. THE OLD CHECK ({PRE_REPAIR_REF[:9]}) — the expectation it computes")
print(f"_catalogue_is_complete() -> {head._catalogue_is_complete()!r}")
print(f"len(CLAIMED_CONTROLS)={len(head.CLAIMED_CONTROLS)}  len(MUTANTS)={len(head.MUTANTS)}")
print("set(CLAIMED_CONTROLS) - {mutant names} = "
      f"{sorted(set(head.CLAIMED_CONTROLS) - {m[0] for m in head.MUTANTS})!r}")
print("{mutant names} - set(CLAIMED_CONTROLS) = "
      f"{sorted({m[0] for m in head.MUTANTS} - set(head.CLAIMED_CONTROLS))!r}   <- never checked")
print("\nDIAGNOSIS: the old check takes NO argument and reads NO file. Its answer is a function")
print("of two literals inside mutate.py alone, so it is identical on ANY tree — including one")
print("with the four controls physically deleted. That is the circularity, executed.")

banner("B. THE OLD CHECK, pointed at the GUTTED tree")
print(f"_catalogue_is_complete() -> {head._catalogue_is_complete()!r}   <- still certifies")
print("(nothing to point: the function has no tree parameter. M22 confirmed the whole harness")
print(" then ran green, killed all 17, and printed 'every catalogued control is genuinely")
print(" verified' on exactly this tree.)")

banner("C. THE REPAIRED CHECK — current tree")
claims = new.load_claims()
names = [m[0] for m in new.MUTANTS]
code, lines = new.catalogue_report(claims, names, root=ROOT)
print("\n".join(lines))
print(f"exit={code}")

banner("D. THE REPAIRED CHECK — the GUTTED tree (the known-broken case)")
code_g, lines_g = new.catalogue_report(claims, names, root=tree)
print("\n".join(lines_g))
print(f"exit={code_g}")
print("\nHONEST READ: it refuses to certify the gutted tree. It does so because those controls")
print("have NO MUTANT, not because the code is gone — a claims-based check cannot see deleted")
print("code. Deletion becomes visible once the mutants exist (Phase 5), via SURVIVED.")

banner("E. MIRROR-IMAGE ATTACK ON THE REPAIRED CHECK ITSELF")
print("A completeness check's vacuity mode is not a pinned axis, it is a register that has")
print("collapsed onto the catalogue. I PREDICTED both collapses below would report GREEN.")
print("One does. The other does not, and the prediction was wrong in an informative way:\n")
empty = {}
e_code, e_lines = new.catalogue_report(empty, names, root=ROOT)
print(f"  empty register               -> exit={e_code}, {len(e_lines)} line(s); "
      f"first: {e_lines[0][:72] if e_lines else ''!r}")
collapsed = {n: {"claim": "x", "published_in": []} for n in names}
print(f"  register == the mutant names -> {new.catalogue_report(collapsed, names, root=ROOT)}")
print("\nAn EMPTY register is caught, and it is caught by check (b) — every mutant answers to")
print("a claim — the reverse direction nobody had ever checked. Deleting the register to get a")
print("green run turns all 17 mutants into orphans. That is an emergent property of adding the")
print("reverse check, not something designed for.")
print("\nA COLLAPSED register is NOT caught, and that is the live residual: if every claim id is")
print("simply a mutant name, the check is a tautology again. The check ALONE does not close it.")
print("The guards are assertions in tests/test_mutate_catalogue.py: the register must be")
print("non-empty, and set(claims) - {mutant names} must be non-empty. Stated, not hidden.")

banner("F. ANTI-VACUITY, MEASURED ON THE REAL REGISTER")
print(f"len(register)={len(claims)}  len(MUTANTS)={len(names)}")
print(f"claims NOT in MUTANTS = {len(set(claims) - set(names))}  <- must be > 0")
print(f"anchors checked = {sum(len(c['published_in']) for c in claims.values())}, "
      f"unresolved = {len(new.catalogue_gaps(claims, names, root=ROOT)[2])}")

shutil.rmtree(tmp, ignore_errors=True)
