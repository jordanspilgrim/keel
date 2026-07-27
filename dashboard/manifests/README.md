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

## A second thing they get wrong, found in pass 16 — `intervention_signal_id`

Every manifest records the intervention signal twice: inline as `intervention_signal`, and by
reference as `intervention_signal_id`. **In 13 of the 14 retained manifests the id does not
resolve to that run's signal**, and it fails quietly rather than loudly:

| cited id | manifests | what it actually is in the retained DB |
| --- | --- | --- |
| `6` | 8 manifests, spanning **8 different `run_id`s** | the experiment signal for `run-20260725T071548` only |
| `8` | 3 manifests | an **ephemeral** theme-ranking row — plain sentence, `run_id` NULL, not a structured signal |
| `1` | 2 manifests | no such row |

Same root cause as the section above: **`run_demo --median` retains only the committed run's
database.** Each run's id was correct against its own DB at write time, but four of those DBs
are discarded, so those ids are now interpreted against a database that never contained them.
Because the id space is dense and reused (`themes.persist` clears and reinserts every
ephemeral row on each analytics pass), a stale id lands on an unrelated row and returns a
plausible answer instead of an error — the worst failure mode for a provenance field.

What changed in pass 16:

- `themes.resolve_signal_for_run(conn, id, run_id)` checks the row's **own** `run_id`, so a
  cross-run dereference returns `None` instead of another run's signal.
- `themes.load_signal` no longer assumes the row is JSON. Citing an ephemeral id used to raise
  `JSONDecodeError` out of an API documented as returning `dict | None`.
- A claims test asserts the committed manifest's id resolves, for its own run, to exactly its
  inline `intervention_signal`.

**The four non-committed manifests are left as they are**, for the same reason as their
`offer_effectiveness`: the databases that would make their ids meaningful no longer exist, and
inventing a resolvable id would be fabricating provenance. Read
`intervention_signal_id` in a non-committed manifest as *"an id that was valid against a
database that no longer exists"* — the inline `intervention_signal` in those files is the only
usable record, and per the section above its `offer_effectiveness` is itself pre-repair.

Only `dashboard/manifest.json` and `manifests/manifest-run-20260725T071548.json` — the
committed run, the source of every published figure — have a lineage that resolves.
