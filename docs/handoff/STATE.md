# Keel — state of the work

**As of commit `a103bf8` (pass 16 remediation complete, pass 17 not yet run).**

This is an internal working document for whoever picks the work up next. It is not part of the
portfolio narrative — `README.md`, `BUILD.md` and `docs/` are.

---

## What Keel is

A local, synthetic-data proof-of-concept of an AI customer-retention flywheel on the OpenAI
API, in three parts:

| Part | What it does | Entry point |
| --- | --- | --- |
| **Act** | Cancellation-saver agent: offers a retention deal inside a hard policy envelope | `agent/runtime.py` |
| **Measure** | Self-grading eval harness with a content-hashed spec version | `evals/judge.py` |
| **Learn** | VoC analytics: clusters churn reasons, ranks by loss, proposes the next intervention | `analytics/themes.py` |

The point of the piece is the *seams* — the offer ledger, the server-authoritative output
contract, the pre-registered median, the kill switch — not the model calls.

## Verify the state in about 90 seconds

```bash
.venv/bin/python -m pytest tests/ -q          # expect: 345 passed
.venv/bin/python scripts/mutate.py            # expect: 17/17 KILLED, catalogue complete
git log --oneline -1                          # expect: a103bf8
```

`scripts/mutate.py` is the load-bearing one. It reverts each control in a private copy of the
tree and requires the suite to go red. It refuses to run unless the baseline is green, requires
exit code **1** specifically (not any non-zero, which would let a collection error certify a
control), names the failing tests so a kill is visibly attributable, and refuses to run at all
if any control listed in `CLAIMED_CONTROLS` has no mutant.

## The headline number, and how it was arrived at

**+15.0pp** segment save-rate lift, range **[+6.7, +21.7]**, all five runs positive.

Pre-registered median-of-k: `run_demo.py --median --k=5`, k fixed in advance, fixed seed, every
run counted, the committed figure is the **median** and never the max, odd-k only, and a
structural failure in any run aborts the whole estimate rather than dropping that run.

It was **+23.3pp** before pass 12. The drop is real and was disclosed in advance of the
re-run — the earlier figure came from a defect (`offers.extended()` was missing the
`abandoned` state, so unanswered offers vanished from `offer_made` — every one of them a loss,
purely survivorship-directional).

**Do not re-run `run_demo.py` to "check" a number.** It costs real money and overwrites
committed artifacts. Whatever a single honest run produces is the number.

## Invariants that must not be broken

1. **No metric rigging.** Never re-roll for a favorable result, never tune the simulator to
   manufacture lift. The median-of-k protocol exists to make this structurally hard.
2. **Server-authoritative output.** The model emits a structured contract (acknowledgement
   ENUM + offer kind/terms + account facts *by reference*); the server renders 100% of
   customer-visible text. The model never writes prose a customer sees.
3. **The offer ledger is the single source of truth** for outcome, cooldown, economics and the
   eval envelope. States: `authorized → presented → accepted | rejected | abandoned |
   superseded`. Anything reading offer state reads the ledger.
4. **Batch and live must persist identical ledger evidence** for the same terminal. There is
   one shared transition, `_close_dangling_offer`, and a mutant guarding it.
5. **`intervention_signal` in a manifest is the record of what Act CONSUMED.** Never recompute
   it in place; publish corrections beside it.
6. **The offline suite makes no network calls.** `tests/conftest.py` installs a socket
   tripwire that raises a `BaseException` subclass, so production `except Exception` fail-safes
   cannot swallow it.

## Known-open, disclosed, not fixed

- Pass 16's remaining **LOW** items.
- **Pass 17 has not run.** Nothing in `a103bf8` has been through an independent review.
- The four non-committed retained manifests carry pre-repair `offer_effectiveness` **and** an
  `intervention_signal_id` that does not resolve. Both are disclosed in
  `dashboard/manifests/README.md`; neither is fixable, because the databases that would make
  them meaningful were discarded by `--median`. Inventing values would be fabricating
  provenance.
- `keel.db` is **untracked**. Artifact-consistency tests skip when it is absent (fresh clone,
  CI, mutation harness's copied tree). The underlying logic is covered portably on temp DBs.
