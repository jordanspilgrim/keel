# Retained per-run manifests

`run_demo.py --median --k=5` keeps one manifest per run, named by its `run_id`, so the whole
pre-registered distribution is inspectable rather than just the committed median.

## What is in the parent directory

The five runs of the current committed batch (`dashboard/demo_aggregate.json` → `run_ids`):

| run_id | segment lift |
|---|---|
| `run-20260725T071548` | **+15.0pp** ← committed median, rendered on the dashboard |
| `run-20260725T073018` | +6.7pp |
| `run-20260725T074246` | +6.7pp |
| `run-20260725T075525` | +15.0pp |
| `run-20260725T081103` | +21.7pp |

Plus three pre-`run_id` manifests kept under their original timestamp names, because they
predate the field entirely and so have nothing to rename to. `legacy-pre-r12-naming/` holds an
older batch with its own README.

## One thing these files get wrong, and why it is not being "fixed"

`intervention_signal.offer_effectiveness` in the **four non-committed** manifests still reflects
the R12 `abandoned` bug — an offer presented and never answered was dropped from `offer_made`,
so those save rates are survivorship-inflated (e.g. `run-20260725T073018` reports pause 0.567
over n=30; the committed run, after repair, reports 0.205 over n=44).

**They are left as they are on purpose.** `run_demo.py` retains only the COMMITTED run's
database — the other four are snapshotted and discarded once the median is chosen. Their
offer_effectiveness therefore cannot be recomputed from anything: the underlying conversations
no longer exist. Editing the numbers would mean inventing them, which is worse than leaving a
known-stale record with a note saying so.

The committed run — the only one any published figure comes from, and the only one the
dashboard, Explorer and API read — WAS repaired, by
`scripts/repair_abandoned_offer_made.py`, and its manifest carries the corrected values.
`keel.db.pre-repair` is retained so the correction is reproducible.

**Bottom line:** treat per-run `offer_effectiveness` in the four non-committed manifests as a
historical artifact of the run that produced it, not as a current measurement. The per-run
`lift` figures are unaffected — the `abandoned` bug never touched a `saved` row, so no
save-rate lift moved.
