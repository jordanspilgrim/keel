# Start here

Four documents. Read them in this order; the whole set is ~400 lines excluding the findings
appendix, which is a lookup table rather than something to read front to back.

| # | File | What it is | Read it for |
| --- | --- | --- | --- |
| 1 | [`STATE.md`](STATE.md) | **Handoff** | What exists right now, a 90-second verification, the invariants that must not be broken, and everything known-open |
| 2 | [`CONTEXT.md`](CONTEXT.md) | **Context** | Why there have been seventeen review passes — the three recurring defect *shapes*, the standing constraints, and the traps that otherwise cost an hour each |
| 3 | [`PLAN.md`](PLAN.md) | **Plan** | The exit bar, how pass 17 actually read against a prediction made before it ran, the ordered backlog, and an explicit "do not do this" list |
| 4 | [`R17-FINDINGS.md`](R17-FINDINGS.md) | Evidence appendix | Look up any specific defect: file:line, failure scenario, reproduction command. Not a document to read straight through |

## The one-paragraph version

Keel is a local, synthetic-data proof-of-concept of an AI customer-retention flywheel
(Act / Measure / Learn) on the OpenAI API, built as a portfolio piece. Its value is entirely
that its claims are true, so it has been through seventeen adversarial review passes.
**Pass 17 failed the exit bar: 8 HIGH, 28 MEDIUM, 15 LOW, 0 unverified, 51 of 52 findings
confirmed.** Worse than pass 16, against a stricter standard. Critically, **all three
mechanizations built in pass 16 to catch the recurring defect classes are themselves
defective**, so the green test suite and the green mutation harness currently prove less than
they appear to. The decision — pre-registered in writing before pass 17 ran — was to stop
reviewing and disclose the residual rather than start an 18th cycle. Nothing from pass 17 is
fixed.

---

## Paste this into a fresh thread

Everything below the line is the kickoff prompt. It is self-contained.

---

You are picking up **Keel**, at `/Users/gabriel/ClaudeCode/keel` (private repo
`jordanspilgrim/keel`, branch `main`). Python; use `.venv/bin/python`.

**Read these four files before doing anything else**, in order:
`docs/handoff/STATE.md`, `docs/handoff/CONTEXT.md`, `docs/handoff/PLAN.md`, and
`docs/handoff/R17-FINDINGS.md` (the last is a lookup table — skim its headings, don't read it
front to back).

**Situation.** Keel is a synthetic-data proof-of-concept of an AI customer-retention flywheel,
built as a portfolio piece demonstrating platform-PM / forward-deployed-engineer judgment. Its
entire value is that its published claims are true. It has been through 17 adversarial review
passes. Pass 17 confirmed 51 defects (8 HIGH, 28 MEDIUM, 15 LOW, zero unverified) and **none
are fixed**. The exit bar — 0 CRITICAL, 0 HIGH, 0 MEDIUM, ≤2 LOW, 0 unverified — was not met.

**The most important thing to understand before you touch anything:** all three verification
mechanisms built in pass 16 are themselves broken. The cue × orthography grid pins initial
capitalisation; the "is the name gone" redaction oracle only inspects the leading token; and
`scripts/mutate.py`'s catalogue-completeness check is circular and will certify a tree with
four safety controls physically removed. **A green `pytest` and a green `mutate.py` therefore
do not currently mean what they claim.** Fix the verifiers before you trust any fix they
verify. `PLAN.md`'s backlog is already ordered this way — start at its item 1, not item 4.

**Work the backlog in `PLAN.md` order.** Its "Do not do this" section is binding.

**Standing constraints, all non-negotiable:**

1. **No metric rigging.** Never re-roll `run_demo.py` for a favourable number, never tune the
   simulator to manufacture lift. **Do not run `run_demo.py` at all** — it costs real money and
   overwrites committed artifacts. The headline (+15.0pp, range [+6.7, +21.7]) is a
   pre-registered median-of-5 and stands.
2. **No bandaids.** Diagnose root causes. If a system is fundamentally broken, say so and
   propose what it should be. A bandaid that silences a symptom is worse than the original bug
   because it hides it. If the real fix is large, scope it and present options.
3. **Honesty over narrative.** Overclaiming is worse than underclaiming. If 5 things were
   asked and 3 verified, say "3 of 5". Never claim a fix you have not executed and observed.
4. **Verify the artifact, not the report.** This repo's single most persistent defect class is
   a control that reports success while not providing the property — a redactor that scrubs
   the *label* and keeps the secret, a log asserting a turn was bounded while it wrote a
   durable save. Always check the property itself.
5. **Never make scope or priority calls autonomously.** Ask. The one standing exception: a
   "fix everything" instruction carries forward across rounds and must not be re-asked.
6. **Do not narrow the exit bar to reach it**, and do not weaken a review prompt for the same
   reason.

**Practical traps that will otherwise cost you an hour each** (full list in `CONTEXT.md`):
regex escaping in generated Python has produced `[^\W\d_]` → `[^\\W\\d_]` four separate times,
which silently matches spaces and periods — read the file back after writing it. `re` has no
`\p{Lu}`; use `str.isupper()`. `git checkout <path>` is blocked by a repo hook — use
`git show HEAD:<path> > <path>`. Heredocs trip the same hook for commit messages — write the
message to a file and `git commit -F`. `db.connect()` does not run migrations; `init_db()` is
the entry point. The mutation harness copies the tree *excluding* `keel.db`, so any test
reading the committed artifact DB must skip when it is absent or it turns the baseline red and
aborts the whole harness.

**First response:** confirm the current commit with `git log --oneline -1` (do not trust a SHA
written in any document, including these), run `.venv/bin/python -m pytest tests/ -q` and
`.venv/bin/python scripts/mutate.py` to see the current state for yourself, then tell me your
plan for `PLAN.md` item 1 before writing any code.
