# Keel — project descriptor

```yaml
name: keel
stack: "Python 3.14 + OpenAI SDK; pytest 8; SQLite (keel.db, untracked); FastAPI/uvicorn local console (port 8500); static dashboard export"
base_ref: origin/main
worktree_isolation: true
check_cmd: none # no ruff/mypy/flake8/black/pyright installed or declared — do NOT invent one
test_cmd: ".venv/bin/python -m pytest tests/ -q" # DELIBERATELY RED: expect exit 1, 449 collected, 1 named fairness failure (the multi-token residual) — see 'Both gates are RED on purpose' below. Do NOT revert the gate to make it green.
definition_of_done: "pytest green AND scripts/mutate.py reports all mutants killed — BUT NOT YET, and do not chase either one today: both gates are DELIBERATELY RED mid-remediation (see 'Both gates are RED on purpose' below), so a green result right now means a gate was reverted, not that the work is done. Green is the Phase 1 target, not the current state. A fix is not done until the gate verifying it has itself been attacked with the MIRROR IMAGE of the defect it exists to catch. Exit bar: 0 CRITICAL / 0 HIGH / 0 MEDIUM / <=2 LOW / 0 unverified."
deploy_targets: none
acceptance: none # local POC; there is no deploy and no acceptance environment
product_context: "Portfolio POC of an AI customer-retention flywheel (Act / Measure / Learn) on synthetic data. Its entire value is that its published claims are true, so a false claim is as severe as a crash."
sources_of_truth: "README.md + BUILD.md (the hardening/remediation record), docs/ (incl. docs/testing.html — publishes which controls are tested), dashboard/manifests/README.md (provenance disclosures). The published claims ARE the product; documenter is load-bearing, not advisory."
roster_profile: small
top_tier_model: claude-opus-5
```

## Both gates are RED on purpose — read this before "fixing" either one

**`pytest` fails on one test and `scripts/mutate.py` exits 1. Both reds are the deliverable.**
A green result from either one today means a gate was reverted, not that the work is done.

Pass 17 confirmed all three verification mechanisms were themselves defective — the cue x
orthography grid pinned initial capitalisation while certifying `orthography_symmetric`;
`_name_survives` inspected only the leading token, so scrubbing a given name and leaving the surname
read as CLEAN; and `mutate.py`'s catalogue-completeness check was circular, certifying a tree with
four safety controls physically removed. **All three are repaired and merged (Phase 0, `914f2ae`).**

Repairing them is what turned the gates red. A repaired gate must be demonstrated FAILING against
the defect it exists to catch, before that defect is fixed — otherwise you only learn the suite is
green afterwards, which is the position this whole exercise exists to escape. So Phase 0 stopped
there deliberately, and Phase 1 fixes the code underneath.

- **`pytest`** — 449 collected. `1 failed, 448 passed` with `keel.db` present;
  `1 failed, 445 passed, 3 skipped` without it (any fresh worktree — `keel.db` is
  gitignored, and the 3 skips are the committed-artifact tests). The one failure is
  `tests/test_guardrails.py::test_the_fairness_gate_checks_orthography_not_just_group`.
  Phase 1 closed the single-token half of the leak — a lowercase self-identified name is now
  redacted after a declaration or address cue — and the group-axis gate went green on its own.
  The 120 remaining cells are all MULTI-TOKEN: the continuation-token walk still requires
  uppercase on tokens 2+, so `Emily watson` and `Sofia van Dijk` leave part of the name in the
  transcript under a `types=['name']` all-clear. That gate demands every rate == 1.0, so it stays
  red until the continuation walk is fixed. Two open-class over-redaction residuals are asserted
  as current behaviour in `tests/test_redaction_control.py` so they are counted, not described.
- **`scripts/mutate.py`** — exits 1 with **21 KILLED and 8 SURVIVED** of 29 mutants. Its
  expectation comes from `docs/controls.json` rather than from a literal inside `mutate.py` whose
  keys were the mutant names, so the catalogue is now complete (29 claims = 29 mutants). A
  SURVIVOR means the control can be deleted with the suite unchanged — nothing tests it.
  Counterpart fix: write those tests, the next item.

**Do not close either red by reverting a gate, weakening the grid, skipping the test, deleting
register entries, or relaxing the completeness check.**

## Hard constraints — non-negotiable

1. **NEVER run `run_demo.py`.** It spends real money and overwrites committed artifacts. The
   headline (+15.0pp, range [+6.7, +21.7]) is a pre-registered median-of-5 and stands. This
   overrides the generic "Keel-like POC" example in the harness schema, which is wrong for this repo.
2. **No metric rigging.** Never re-roll for a favourable number; never tune the simulator to
   manufacture lift.
3. **No bandaids.** Diagnose root causes. A bandaid that silences a symptom is worse than the bug.
4. **Verify the artifact, not the report.** The repo's single most persistent defect class is a
   control that reports success while not providing the property.
5. **Honesty over narrative.** Overclaiming is worse than underclaiming. "3 of 5 verified" beats
   a wrong "done".
6. **Never make scope or priority calls autonomously.** Standing exception: a "fix everything"
   instruction carries forward across rounds and must not be re-asked.
7. **Review intensity must stay constant or increase.** Never weaken a review prompt to reach the
   exit bar; diff a new pass's prompts against the previous pass's.

## Traps that otherwise cost an hour each

- **`.venv/` is gitignored**, so a fresh worktree has no interpreter. From inside a worktree, invoke
  the primary venv by absolute path: `/Users/gabriel/ClaudeCode/keel/.venv/bin/python -m pytest tests/ -q`
  (cwd determines which code is collected).
- **Regex escaping in generated Python** has produced `[^\W\d_]` -> `[^\\W\\d_]` four separate
  times, which silently matches spaces and periods. Read the file back after writing it.
- **`re` has no `\p{Lu}`** — use `str.isupper()`. An ASCII `[A-Z]` is the disparate-impact bug that
  was removed.
- **`git checkout <path>` is blocked** by a repo hook. Use `git show HEAD:<path> > <path>`.
- **Heredocs trip the same hook** for commit messages. Write the message to a file, `git commit -F`.
- **`db.connect()` does not run migrations** — `init_db(conn)` is the entry point.
- **The mutation harness copies the tree excluding `keel.db*`**, so any test reading the committed
  artifact DB must skip when absent, or it turns the baseline red and aborts the whole harness.

## Working documents

`docs/handoff/` is the authoritative state: `START-HERE.md` (index), `STATE.md`, `CONTEXT.md`
(the three recurring defect shapes), `PLAN.md` (exit bar + ordered backlog + a binding
"do not do this" list), `R17-FINDINGS.md` (51 confirmed findings; a lookup table).
