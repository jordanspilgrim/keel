# Keel — project descriptor

```yaml
name: keel
stack: "Python 3.14 + OpenAI SDK; pytest 8; SQLite (keel.db, untracked); FastAPI/uvicorn local console (port 8500); static dashboard export"
base_ref: origin/main
worktree_isolation: true
check_cmd: none # no ruff/mypy/flake8/black/pyright installed or declared — do NOT invent one
test_cmd: ".venv/bin/python -m pytest tests/ -q" # expect 345 passed
definition_of_done: "pytest green AND scripts/mutate.py reports all mutants killed — BUT see 'The gates lie' below. A fix is not done until the gate verifying it has itself been attacked with the MIRROR IMAGE of the defect it exists to catch. Exit bar: 0 CRITICAL / 0 HIGH / 0 MEDIUM / <=2 LOW / 0 unverified."
deploy_targets: none
acceptance: none # local POC; there is no deploy and no acceptance environment
product_context: "Portfolio POC of an AI customer-retention flywheel (Act / Measure / Learn) on synthetic data. Its entire value is that its published claims are true, so a false claim is as severe as a crash."
sources_of_truth: "README.md + BUILD.md (the hardening/remediation record), docs/ (incl. docs/testing.html — publishes which controls are tested), dashboard/manifests/README.md (provenance disclosures). The published claims ARE the product; documenter is load-bearing, not advisory."
roster_profile: small
top_tier_model: claude-opus-5
```

## The gates lie — read this before trusting any green result

`pytest` (345 passed) and `scripts/mutate.py` ("every catalogued control is genuinely verified")
both currently report green. Pass 17 confirmed **all three** verification mechanisms are themselves
defective:

- the cue x orthography grid **pins initial capitalisation** (5% coverage on that axis, while
  certifying `orthography_symmetric`);
- `_name_survives` inspects only the **leading token**, so scrubbing a given name and leaving the
  surname reads as CLEAN — the mirror image of the bug it was built for;
- `mutate.py`'s catalogue-completeness check is **circular** and certifies a tree with four safety
  controls physically removed.

A green suite therefore does not currently mean what it claims. Repair the verifier before trusting
any fix it verified.

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
