"""Mutation harness — turn "mutation-verified" from a claim into a command.

WHY THIS EXISTS
---------------
Across three review passes the single most persistent defect class in this repo was not a bug.
It was: **the commit message says a thing was verified; run the verifier and it wasn't.**
Three separate instances survived into pass 14 — "Tested against a real legacy schema" (true of
half the fix), "the script ASSERTS all three" (two were asserted), "parametrized over EVERY
input" (three inputs were missing). That class is invisible to a green test suite BY
CONSTRUCTION, because a passing suite is exactly what it looks like.

The fix is not another review pass. It is to make the claim executable: state each control and
the test that guards it, then have a script revert the control and check the suite goes red.

USAGE
    .venv/bin/python scripts/mutate.py            # run every mutant
    .venv/bin/python scripts/mutate.py --list     # show the catalogue
    .venv/bin/python scripts/mutate.py -k ceiling # run matching mutants only

Exit code 0 iff every mutant was KILLED. A SURVIVED mutant means the control it names can be
deleted with a green suite — i.e. nothing actually verifies it.

Each entry is (name, file, exact-string-to-replace, replacement, what-it-should-break). The
exact-string form is deliberate: it fails loudly when the code moves, rather than silently
mutating nothing and reporting a false KILL.

SAFETY: mutations are applied to a private copy of the tree under a uniquely-named temp
directory, never to tracked files. Pass 14 observed a shared scratchpad being restored
underneath a running test, producing a false "all passed" — so the copy is verified to contain
the mutation immediately before pytest runs and to be unchanged after.

THE ORACLE, and why it is not just the exit code (pass 15 found this in THIS file).
The first version decided KILLED from `pytest`'s return code alone, with no baseline. That is
wrong in three ways, each of which makes the harness certify a control it never tested:
  * On an ALREADY-RED tree every mutant "kills" and it prints "every catalogued control is
    genuinely verified." Reproduced: one unrelated failing test added, 10/10 KILLED.
  * A collection or import error also exits non-zero, so a mutant that runs ZERO tests
    certifies its control.
  * A mutant can be killed by a test that has nothing to do with the control it names.
So: a baseline run must be GREEN before any mutant runs; a kill requires exit code 1
specifically (test failures, not collection errors); and the failing tests are reported so a
reader can see the kill is attributable rather than incidental.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (name, relpath, find, replace, control it should break)
MUTANTS: list[tuple[str, str, str, str, str]] = [
    ("extended_drops_abandoned", "agent/offers.py",
     'for state in ("accepted", "presented", "rejected", "abandoned"):',
     'for state in ("accepted", "presented", "rejected"):',
     "R13 CRITICAL: an unanswered offer vanishes from offer_made, analytics and cooldown"),

    ("ceiling_fallback_upgrades", "agent/runtime.py",
     "    live = offers.presented(rec[\"offers\"])\n    if live is not None and live.kind == kind:",
     "    live = None\n    if live is not None and live.kind == kind:",
     "R13 D8d: a failure path re-presents an already-seen offer at its higher ceiling"),

    ("ssn_cue_drops_is", "agent/guardrails.py",
     r'(?:\s+is)?[:\s#]*)\d{9}\b',
     r'[:\s#]*)\d{9}\b',
     "R13 D8b: 'My SSN is 123456789', the commonest phrasing, leaks"),

    ("unknown_tool_wording", "agent/runtime.py",
     'or "unknown action tool" in _r)',
     'or "unknown tool" in _r)',
     "R13 D8h: unknown tools land on the 'Over-ceiling offers capped' safety tile"),

    ("fulfillment_insert_or_ignore", "agent/runtime.py",
     '        conn.execute(\n            "INSERT INTO offer_fulfillment_requests',
     '        conn.execute(\n            "INSERT OR IGNORE INTO offer_fulfillment_requests',
     "R13 D8f: the durable write becomes a silent no-op on a legacy schema"),

    ("hash_drops_scope_classifier", "agent/guardrails.py",
     "        _SCOPE_INSTRUCTIONS, repr(_SCOPE_SCHEMA),",
     "        config.MINI_MODEL,",
     "R12 D5: gutting the scope prompt leaves the kill switch green at a 0.714 true catch rate"),

    ("hash_drops_pii_replacement", "agent/guardrails.py",
     "repr(sorted((rx.pattern, rx.flags, repl, kind) for rx, repl, kind in _PII_PATTERNS)),",
     "repr(sorted((rx.pattern, kind) for rx, _repl, kind in _PII_PATTERNS)),",
     "R14 H2: a neutered redaction token leaks while still reporting as caught"),

    ("close_dangling_batch", "agent/runtime.py",
     "    _close_dangling_offer(rec, outcome)   # same transition the live path applies (no drift)",
     "    pass  # _close_dangling_offer removed",
     "R12 E2E#14: batch and live persist different ledger evidence for the same terminal"),

    ("resolving_admission_guard", "server.py",
     '        if session.get("_resolving"):\n            # The guard was one-directional',
     '        if False and session.get("_resolving"):\n            # The guard was one-directional',
     "R12 E2E#3: a turn is admitted mid-resolve and races the finalize"),

    ("name_token_ascii_only", "agent/guardrails.py",
     '_NAME_TOK = r"[^\\W\\d_][^\\W\\d_\'’\\-]*(?:[\'’\\-][^\\W\\d_][^\\W\\d_\'’\\-]*)*"',
     '_NAME_TOK = r"[A-Z][a-z]+"',
     "R15: name redaction becomes 100% ASCII / 0% diacritics, apostrophes, non-Latin"),

    ("credentials_keep_the_value", "agent/guardrails.py",
     '        out = _CREDENTIALS.sub(r"\\1[REDACTED_SECRET]", out)',
     '        pass  # credential value left in place',
     "R15: a password/API key/routing number is stored verbatim in the transcript"),

    ("db_rebuild_skipped", "db.py",
     "    _relax_not_null_for_pre_persist_queues(conn)",
     "    pass  # migration skipped",
     "R13 D8f: legacy schemas cannot hold a turn-time pre-write"),
]


def _pytest(tree: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
                           "-p", "no:randomly"],
                          cwd=tree, capture_output=True, text=True)


def _failing_tests(out: str) -> list[str]:
    ids = []
    for ln in out.splitlines():
        if ln.startswith("FAILED "):
            ids.append(ln[len("FAILED "):].split(" - ")[0].strip())
    return ids


def _baseline_is_green() -> tuple[bool, str]:
    """The unmutated tree MUST be green, or every 'kill' below is meaningless."""
    tmp = tempfile.mkdtemp(prefix="keel-mut-baseline-")
    tree = os.path.join(tmp, "tree")
    shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", "*.pyc", "keel.db*", ".pytest_cache"))
    proc = _pytest(tree)
    shutil.rmtree(tmp, ignore_errors=True)
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
    return proc.returncode == 0, tail[0].strip()


def _run(mut, keep: bool) -> tuple[str, bool, str]:
    name, rel, find, repl, control = mut
    tmp = tempfile.mkdtemp(prefix=f"keel-mut-{name}-")
    tree = os.path.join(tmp, "tree")
    shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", "*.pyc", "keel.db*", ".pytest_cache"))
    path = os.path.join(tree, rel)
    src = open(path).read()
    if src.count(find) != 1:
        shutil.rmtree(tmp, ignore_errors=True)
        return name, False, f"ANCHOR MISS ({src.count(find)} matches) — the code moved; update the catalogue"
    open(path, "w").write(src.replace(find, repl))
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()

    proc = _pytest(tree)
    # the mutation must still be present — guards against a concurrent restore (see docstring)
    if hashlib.sha256(open(path, "rb").read()).hexdigest() != digest:
        shutil.rmtree(tmp, ignore_errors=True)
        return name, False, "TREE MUTATED UNDER THE RUN — result discarded"
    failing = _failing_tests(proc.stdout)
    # Exit code 1 means TEST FAILURES. 2 is a collection/usage error, 3 internal, 4 usage,
    # 5 no tests collected — a mutant that breaks the import runs zero tests and must not be
    # credited with a kill.
    killed = proc.returncode == 1 and bool(failing)
    if proc.returncode not in (0, 1):
        detail = f"NOT A KILL — pytest exit {proc.returncode} (collection/import error, zero tests ran)"
    elif killed:
        shown = ", ".join(t.split("::")[-1] for t in failing[:3])
        detail = f"{len(failing)} test(s) failed: {shown}{' …' if len(failing) > 3 else ''}"
    else:
        tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
        detail = tail[0].strip()
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return name, killed, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-k", default="", help="substring filter on mutant name")
    ap.add_argument("--keep", action="store_true", help="keep temp trees for inspection")
    args = ap.parse_args()

    chosen = [m for m in MUTANTS if args.k in m[0]]
    if args.list:
        for name, rel, _f, _r, control in chosen:
            print(f"  {name:32} {rel:22} {control}")
        return 0

    ok, base_detail = _baseline_is_green()
    print(f"baseline (unmutated): {base_detail}")
    if not ok:
        print("\nABORTING — the baseline suite is NOT green, so every 'kill' below would be "
              "meaningless: a mutant cannot be shown to break something that is already broken. "
              "Fix the suite first.")
        return 2
    print(f"running {len(chosen)} mutant(s) — each must be KILLED (suite goes red)\n")
    survived, stale = [], []
    for mut in chosen:
        name, killed, detail = _run(mut, args.keep)
        tag = "KILLED  " if killed else ("ANCHOR  " if detail.startswith("ANCHOR") else "SURVIVED")
        print(f"  {tag}  {name:32} {detail}")
        if detail.startswith("ANCHOR"):
            stale.append((name, detail))
        elif not killed:
            survived.append((name, mut[4]))
    print()
    if stale:
        # NOT a coverage claim either way — the catalogue is out of date and says nothing
        # about the control. Reporting it under "can be deleted" was itself misleading.
        print(f"{len(stale)} STALE ANCHOR(S) — the catalogue no longer matches the code, so these "
              f"controls were NOT tested in either direction:")
        for name, detail in stale:
            print(f"    {name}: {detail}")
    if survived:
        print(f"{len(survived)} SURVIVED — these controls can be deleted with a green suite:")
        for name, control in survived:
            print(f"    {name}: {control}")
    if survived or stale:
        return 1
    print("all mutants killed — every catalogued control is genuinely verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
