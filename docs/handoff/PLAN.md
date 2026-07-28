# Keel — plan from here

Read [`STATE.md`](STATE.md) first for where things stand, and [`CONTEXT.md`](CONTEXT.md) for
why the review loop looks the way it does.

---

## The exit bar

A full-strength, 8-dimension adversarial pass returning:

- **0 CRITICAL**
- **0 HIGH**
- **0 MEDIUM**
- **≤ 2 LOW**
- **0 unverified findings**

That last one is not decoration. Pass 15's harness bucketed **null** verdicts as "refuted" and
reported 19 refutations that were in fact 19 findings nobody had checked. Any pass whose
verification stage returns nulls has **not** met the bar, regardless of the headline counts.

## How pass 17 actually read

**It ran. The bar was not met, and not narrowly: 0 CRITICAL, 8 HIGH, 28 MEDIUM, 15 LOW,
0 unverified. 51 of 52 findings confirmed.** Full list in [`R17-FINDINGS.md`](R17-FINDINGS.md).

Against the decision rule written below *before* the run:

- The prediction was right about **where** — the two highest-severity fairness findings are in
  `_probe`/`_name_survives`, code `a103bf8` had just written.
- The prediction was wrong about **scale**. Pass 16 found 1C/4H/9M/4L; pass 17 found
  8H/28M/15L against a *stricter* verification standard. This is not a converging sequence.
- The clause that fires is: *"A finding in a class that has a mutant → treat the mechanization
  as broken."* All three pass-16 mechanizations are defective:
  - the cue × orthography grid **pins initial capitalisation** (HIGH);
  - the "is the name gone" oracle checks only the **leading token**, so it cannot see the
    mirror image of the partial redaction it was built for (HIGH);
  - `mutate.py`'s catalogue-completeness check is **circular** and certifies a tree with four
    safety controls physically removed (MEDIUM).

**Decision: stop reviewing, disclose the residual.** This is the pre-registered outcome, not a
retreat under budget pressure — though budget is also nearly gone. Continuing would mean a
19th pass reviewing fixes written under the same conditions that produced these, and the
evidence now says each pass's remediation introduces roughly as much as it closes.

### What this outcome actually demonstrates

The honest framing for a reader, and the one this repo should stand on:

- A 17-pass adversarial loop with an **explicit, pre-registered exit bar** that was **not
  reached**, and is reported as not reached rather than quietly redefined.
- A prediction written **before** the run and checked against it, including the half that was
  wrong.
- Safety controls whose verification is a command — and a documented case where **the
  verifier itself was the defect**, three times over.

That is a more useful artifact than a clean 17th pass would have been. A clean pass would have
meant the loop stopped finding things; this one shows exactly where the method's floor is.

---

## The original pre-registered rule (kept verbatim, for the record)

```
8 dimensions, in parallel  →  refute-by-default verification  →  synthesis
```

Requirements carried forward:

1. **Diff pass 17's prompts against pass 16's** and show intensity did not drop. Reaching the
   bar by softening the question is the one failure mode that would invalidate the whole
   exercise.
2. **Null verdicts count as UNVERIFIED**, never as refuted.
3. **Do not run `run_demo.py`.** Real money, and it overwrites committed artifacts.

### What pass 17 is most likely to find, stated in advance

Recording the prediction is the point — it is checkable afterwards, unlike a retrospective
claim that the loop was converging.

- **Most likely:** something in what `a103bf8` changed. Freshly-touched code has been the
  highest-yield surface in every pass. Specifically `_probe`/`_name_survives` in
  `evals/agent_fairness.py` and `resolve_signal_for_run` in `analytics/themes.py`.
- **Also plausible:** the artifact tests' skip-when-absent behaviour hides a real regression on
  a machine without `keel.db`. The logic is covered on temp DBs, but the *artifact* assertions
  are only checked where the artifact exists.
- **Less likely now:** another name-redaction or credential defect. Those are the two classes
  with grid coverage and mutants; if one appears anyway, the mechanization did not work and
  that is a much more important finding than the bug.

### How to read the result

- **LOW-only** → genuine convergence. Close it out and ship.
- **A HIGH in code `a103bf8` touched, despite the three gates** → the loop has found its floor.
  The correct move is to **disclose the residual and stop**, not to spend another window. Say
  so plainly rather than continuing on momentum.
- **A finding in a class that has a mutant** → treat the mechanization as broken. Fix the
  mutant before fixing the bug, otherwise the same class returns.

## Backlog for whoever picks this up

Ordered by what unblocks the most. **Do not start at #4** — the first three determine whether
any later fix can be trusted.

| # | Item | Why first |
| --- | --- | --- |
| 1 | **Repair the three mechanizations** (grid pins capitalisation; oracle checks only the leading token; `mutate.py`'s circular catalogue check) | Until these work, a green `mutate.py` proves nothing, and every fix below gets verified by a broken verifier |
| 2 | **Fix the socket tripwire** (bytes host, `connect_ex`, DNS) | The offline suite's core claim — "no network" — is currently false |
| 3 | **Add tests for the untested kill-switch floors** (eval pass rate, coverage, freshness) | The kill switch is the top-level safety control and its primary triggers never execute |
| 4 | The two guardrail HIGHs (`_SENSITIVE_TERMS` label-only scrub; credential separator case) | Real always-on leaks, but fix them *after* #1 so the fix is actually verified |
| 5 | The remaining HIGHs, then MEDIUMs, then LOWs, per `R17-FINDINGS.md` | |
| 6 | Re-run the dashboard export | `data.js` still contradicts the repaired `manifest.json` |

**A caution earned the hard way, from this session:** the pass-16 remediation of M5 introduced
a script that corrupted three historical manifests, caught only by inspecting the *result*
rather than trusting the script's own output. Every fix above touches something a published
claim depends on. Verify the artifact, not the report.

## Do not do this

- **Do not run another review pass before #1–#3.** That is what the last four passes did.
- **Do not re-run `run_demo.py`.** Real money, overwrites committed artifacts, and the
  headline is pre-registered.
- **Do not "fix" the +23.3pp → +15.0pp attribution by inventing a cause.** See `STATE.md` —
  the correct statement is that no controlled attribution has been done.
- **Do not narrow the exit bar to reach it.**

## What "done" looks like for this piece

It is a portfolio artifact demonstrating platform-PM / forward-deployed-engineer judgment. The
demonstration is **not** "the agent saves customers" — it is:

- a metric with a **pre-registered** protocol that produced a number **lower** than the first
  one reported, and said so;
- safety controls whose verification is a **command** rather than a claim;
- a review loop that converged, was measured while converging, and whose residual is
  **disclosed rather than buried**.

A reader who runs `pytest` and `scripts/mutate.py` and reads
`dashboard/manifests/README.md` should be able to tell exactly what is true, what is stale,
and what is unfixable — without taking anything on trust. If that holds, the piece is done,
whether or not pass 17 is perfectly clean.
