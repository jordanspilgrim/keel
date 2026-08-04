# Keel — agent operating notes

Full descriptor: [`.claude/project.md`](.claude/project.md). Authoritative state:
[`docs/handoff/START-HERE.md`](docs/handoff/START-HERE.md).

## Setup (fresh clone / new machine)

```bash
./scripts/harness-bootstrap.sh
```

Installs the shared agent harness into `~/.claude` (roster, `/kickoff`, `/standup`, DLB + branch
hooks). Without it, `.claude/project.md` has nothing to drive. Deliberately not automatic.

## The three things that will bite you

1. **NEVER run `run_demo.py`.** Real money, and it overwrites committed artifacts. The headline
   (+15.0pp, range [+6.7, +21.7]) is a pre-registered median-of-5 and stands. This overrides the
   generic "Keel-like POC" example in the harness schema, which names `run_demo.py` as the
   definition of done. It is wrong for this repo.

2. **The gates were lying. They are now RED on purpose.** Pass 17 confirmed all three verification
   mechanisms were themselves defective — the orthography grid pinned capitalisation, the redaction
   oracle read only the leading token, and `mutate.py`'s completeness check was circular. All three
   are repaired (Phase 0, merged). Repairing them is what turned both gates red: they now fail
   against defects that were always there and that they previously could not see. **A green result
   from either gate today means something was reverted, not that the work is done.** See *Gates*
   below before acting on a failure.

3. **Verify the artifact, not the report.** The repo's most persistent defect class is a control
   that reports success while not providing the property. Check *is the secret gone*, never *did
   something say it was*.

## Gates

```bash
.venv/bin/python -m pytest tests/ -q      # expect exit 1, 2 FAILED — see below
.venv/bin/python scripts/mutate.py        # expect exit 2, CATALOGUE INCOMPLETE — see below
```

**BOTH GATES ARE EXPECTED TO BE RED RIGHT NOW. BOTH REDS ARE THE DELIVERABLE, NOT A REGRESSION.**
A repaired gate has to be shown FAILING against the defect it exists to catch, before that defect
is fixed — otherwise you never learn whether it would have caught anything. So Phase 0 repaired the
gates and deliberately stopped there; Phase 1 fixes the code underneath them and turns them green.

**`pytest` is EXPECTED to fail on exactly two tests.** Phase 0.1 unpinned the fairness grid's case
axis and 0.2 made the redaction oracle check every token instead of the leading one. Both repaired
gates now fail against the live redactor, which still leaks a lowercase self-identified name
(`"my name is emily"` is not redacted, and reports `types=[]`). **That failure is the proof the
repair works.** The two, by name:

```
FAILED tests/test_agent_fairness.py::test_the_probe_covers_a_cue_grid_not_a_single_phrasing
FAILED tests/test_guardrails.py::test_the_fairness_gate_checks_orthography_not_just_group
```

387 collected. **With `keel.db` present** (the primary tree): `2 failed, 385 passed`. **Without it**
(any fresh worktree — `keel.db` is gitignored): `2 failed, 382 passed, 3 skipped`, the 3 skips being
the committed-artifact tests. Two threads read different numbers off this and both were right; state
which tree you ran in.

**Do not "fix" this by reverting the fairness gate, weakening the grid, skipping the two tests, or
relaxing their assertions.** The counterpart code fix is Phase 1 (`agent/guardrails.py`).

**`mutate.py` is EXPECTED to exit 2 right now, and that redness is deliberate.** Phase 0.3
repaired its completeness check: the expectation now comes from `docs/controls.json`, the
register of controls this repo publicly claims, instead of from a literal inside `mutate.py`
whose keys were the mutant names. The repaired check immediately names 12 publicly-claimed
controls that have no mutant — which is the finding R17 M22 predicted and the old check could
not see. Adding those mutants is Phase 5 and is blocked on this repair (adding mutants while
the check was circular would extend the mechanism 0.3 exists to break).

**Do not "fix" this by reverting the gate, deleting register entries, or relaxing the check.**

There is no lint or type gate: ruff, mypy, flake8, black and pyright are neither installed nor
declared. Do not invent one.

From inside a worktree `.venv/` does not exist (it is gitignored) — call the primary interpreter by
absolute path: `/Users/gabriel/ClaudeCode/keel/.venv/bin/python -m pytest tests/ -q`.
