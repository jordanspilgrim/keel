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
.venv/bin/python -m pytest tests/ -q      # expect exit 1, 1 FAILED — see below
.venv/bin/python scripts/mutate.py        # expect exit 0, 30/30 KILLED — see below
```

**`pytest` IS EXPECTED TO BE RED. `mutate.py` IS NOW GREEN, AND THAT IS NEW.**
A repaired gate has to be shown FAILING against the defect it exists to catch, before that defect
is fixed — otherwise you never learn whether it would have caught anything. So Phase 0 repaired the
gates and deliberately stopped there; Phase 1 fixes the code underneath them and turns them green.

**`mutate.py` has now reached that point and prints _"every catalogued control is genuinely
verified"_.** Treat that sentence with the history it carries: R17 M22 measured it printing on a
tree with four safety controls physically deleted, by a completeness check that could not detect
its own falsity. What makes it mean something now is that the check reads an independent register
(`docs/controls.json`), the catalogue is complete at 30 = 30, and every kill is attributed to a
failure the baseline did not already have. **If it ever prints on a red or incomplete run again,
that is a defect, not a pass.**

**`pytest` is EXPECTED to fail on exactly one test.** Phase 0.1 unpinned the fairness grid's case
axis and 0.2 made the redaction oracle check every token instead of the leading one. Phase 1 then
closed the single-token half of the leak the repaired gates exposed — `"my name is emily"` now
redacts and reports `types=['name']` — which took the group-axis gate green. **That gate going
green on its own, with no test edited to make it, is the proof the repair works.** The one that
remains, by name:

```
FAILED tests/test_guardrails.py::test_the_fairness_gate_checks_orthography_not_just_group
```

**Phase 1 closed the single-token half of the leak, so the group-axis gate
(`test_the_probe_covers_a_cue_grid_not_a_single_phrasing`) is now GREEN and only the orthography
gate remains red.** The residual is 120 of 408 cells and every one is MULTI-TOKEN: the
continuation-token walk still requires uppercase on tokens 2+, so `Emily watson` and
`Sofia van Dijk` leave part of the name in the transcript. Single-token leaks are 0 across every
cue. That gate asserts every orthography rate == 1.0, so it stays red until the continuation walk
is fixed — **a still-red gate here is the known residual, not evidence a fix failed.**

457 collected. **With `keel.db` present** (the primary tree): `1 failed, 456 passed`.
**Without it** (any fresh worktree — `keel.db` is gitignored): `1 failed, 453 passed, 3 skipped`, the 3 skips being the committed-artifact tests. Two threads read different numbers off
this and both were right; state which tree you ran in.

**Do not "fix" this by reverting the fairness gate, weakening the grid, skipping the test, or
relaxing its assertions.** The counterpart code fix is the continuation-token walk in
`agent/guardrails.py`.

**`mutate.py` is EXPECTED to exit 1 right now, and that redness is deliberate.** Phase 0.3
repaired its completeness check: the expectation now comes from `docs/controls.json`, the
register of controls this repo publicly claims, instead of from a literal inside `mutate.py`
whose keys were the mutant names. The repaired check named 12 publicly-claimed controls with
no mutant — the finding R17 M22 predicted and the old check could not see. **All 12 mutants now
exist, and 7 of them SURVIVE**: those controls can be deleted with the suite unchanged, because
nothing tests them. Among them are all three kill-switch floors, the money demo's independent
variable, and all three judge-calibration gates. Writing those tests is the next item.

One further row is NOT part of the 12: `proxy_probe_single_cue` SURVIVES, but it is **masked
rather than unguarded**. Measured — its guard is `test_the_probe_covers_a_cue_grid_not_a_single
_phrasing`, which asserts `len(_PROBE_CUES) >= 4` and does catch the mutation; that test is
simply already failing at baseline on a different assert, so no NEW failure appears. It should
kill again once Phase 1 turns the redactor green. `proxy_boolean_oracle` was an ANCHOR MISS for
the same window (0.2 rewrote `_name_survives`) and its anchor is now re-pointed and killing.

**Do not "fix" this by reverting the gate, deleting register entries, or relaxing the check.**

There is no lint or type gate: ruff, mypy, flake8, black and pyright are neither installed nor
declared. Do not invent one.

From inside a worktree `.venv/` does not exist (it is gitignored) — call the primary interpreter by
absolute path: `/Users/gabriel/ClaudeCode/keel/.venv/bin/python -m pytest tests/ -q`.
