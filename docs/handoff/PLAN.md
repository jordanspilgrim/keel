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

## Next step: pass 17

Not yet run. Nothing in `a103bf8` has been through independent review.

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

## Backlog, in priority order

| # | Item | Notes |
| --- | --- | --- |
| 1 | **Run pass 17** | The gate on everything below |
| 2 | Pass 16's remaining **LOW** items | Deferred openly, never silently |
| 3 | Fold pass 17's results into `STATE.md` | Keep the handoff true |
| 4 | Decide ship / disclose-and-stop | Owner's call, per the read above |

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
