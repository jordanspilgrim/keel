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

2. **The gates currently lie.** `pytest` (345 passed) and `scripts/mutate.py` ("every catalogued
   control is genuinely verified") both report green, and pass 17 confirmed all three verification
   mechanisms are themselves defective — the orthography grid pins capitalisation, the redaction
   oracle reads only the leading token, and `mutate.py`'s completeness check is circular. Repair the
   verifier before trusting anything it verified.

3. **Verify the artifact, not the report.** The repo's most persistent defect class is a control
   that reports success while not providing the property. Check *is the secret gone*, never *did
   something say it was*.

## Gates

```bash
.venv/bin/python -m pytest tests/ -q      # expect 345 passed
.venv/bin/python scripts/mutate.py        # expect all mutants killed
```

There is no lint or type gate: ruff, mypy, flake8, black and pyright are neither installed nor
declared. Do not invent one.

From inside a worktree `.venv/` does not exist (it is gitignored) — call the primary interpreter by
absolute path: `/Users/gabriel/ClaudeCode/keel/.venv/bin/python -m pytest tests/ -q`.
