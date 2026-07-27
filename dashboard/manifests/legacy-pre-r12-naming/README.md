# Legacy manifests (pre-R12 naming)

These 23 manifests were written before `run_demo.py` named retained manifests by `run_id`.
Under the old scheme the filename carried the timestamp of the run that *triggered the write*,
not the run whose results the file contains, so a verifier indexing by filename read the wrong
result. They have been **renamed here to match their internal `run_id`**, so filename and
content now agree. Nothing reads these files programmatically.

## What these 23 files actually are — corrected 2026-07-25

An earlier version of this README said all 23 "are the full k=5 distribution behind the earlier
published estimate, and reading their CONTENTS reproduces the disclosed median of +11.7pp and
range [+5.0, +18.3] exactly." **That was false, and self-contradictory on its face** — 23 files
cannot be a k=5 distribution. Reading all 23 gives median **+16.7pp**, range **[-10.0, +45.0]**.

Only **five** of them are the pre-registered batch behind the +11.7pp estimate. They are, and
they do reproduce it exactly:

| run_id | segment lift |
|---|---|
| `run-20260724T032049` | +5.0pp |
| `run-20260724T033415` | +11.7pp |
| `run-20260724T034654` | +13.3pp |
| `run-20260724T035915` | +18.3pp |
| `run-20260724T041318` | +11.7pp |

→ median **+11.7pp**, range **[+5.0, +18.3]** — matching what README.md and BUILD.md publish.

The other **18 are exploratory single runs** from development. They were never part of any
pre-registered estimate, no median was ever computed over them, and none of their numbers has
been published as a result. They are retained rather than deleted because deleting the runs
that came out badly is precisely what a pre-registration exists to prevent.

**Two of those exploratory runs were negative** and are disclosed here rather than left to be
discovered: `run-20260723T052641` at **-10.0pp** and `run-20260723T182340` at **-5.0pp**. Both
predate the paired-cohort and eval-eligibility gates that the pre-registered batches run under,
so they are not comparable to the published estimates — but "not comparable" is a reason to
label them, not a reason to omit them.

## Scope of the published band

README.md and BUILD.md claim every one of the **15 pre-registered runs** (three k=5 batches)
landed in [+5.0, +25.0]pp. That claim covers the 15 pre-registered runs only, and it is true:

- batch 1 `[5.0, 11.7, 11.7, 13.3, 18.3]` → median 11.7
- batch 2 `[10.0, 15.0, 23.3, 25.0, 25.0]` → median 23.3
- batch 3 `[6.7, 6.7, 15.0, 15.0, 21.7]` → median 15.0

It does **not** cover the 18 exploratory runs in this directory, and was never meant to. That
distinction is stated here because a reader who finds a -10.0pp file in a directory linked from
the README deserves to know which population it belongs to.
