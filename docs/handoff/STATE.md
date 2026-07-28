# Keel — state of the work

**Pass 17 has run against `a103bf8` and the exit bar was NOT met: 0 CRITICAL, 8 HIGH,
28 MEDIUM, 15 LOW, 0 unverified — 51 of 52 findings confirmed by refute-by-default
verifiers. Full list: [`R17-FINDINGS.md`](R17-FINDINGS.md). Read that before trusting
anything below.**

Per the decision rule written down in [`PLAN.md`](PLAN.md) *before* pass 17 ran, this
outcome means **stop and disclose the residual**, not fix-and-re-review. The reasoning is
in PLAN.md under "How pass 17 actually read".

To find the current commit, run `git log --oneline -1` — do not trust a SHA written into
a document, including this one.

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

It was **+23.3pp** before pass 12, and the commitment to re-run and publish whatever came
back was made in writing beforehand.

**The cause of the drop is NOT established, and an earlier version of this document got it
wrong.** It attributed the drop to the `offers.extended()` / `abandoned` defect — but the
repair script *asserts* that fix cannot move a save rate (no `saved` row is touched, no
realized margin cost added), and that assert passes. So by the repo's own invariant that
defect cannot be the cause. Pass 17 flagged the contradiction (`metrics-provenance`,
MEDIUM). The two runs differed in more than one way (code changed across passes 12–16), so
the honest statement is: **the earlier figure and the current figure come from different
code, and no controlled attribution of the difference has been done.** Do not repeat the
old explanation.

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

- **All 51 confirmed pass-17 findings.** See [`R17-FINDINGS.md`](R17-FINDINGS.md). None are
  fixed. The eight HIGHs in one line each:
  1. `_SENSITIVE_TERMS` scrubs the health **label** and keeps the value, reporting success —
     the identical bug the adjacent comment says was eliminated for credentials.
  2. The credential regex is defeated by capitalising the separator (`My PASSWORD IS hunter2`
     is not redacted at all).
  3. Published judge-calibration figures were measured under a superseded eval spec *and* a
     superseded golden set, undisclosed.
  4. The orthography grid **pins initial capitalisation**; on that axis coverage is 5% vs
     100%, and the harness certifies `orthography_symmetric`.
  5. `_name_survives` inspects only the **leading** token, so scrubbing the given name and
     leaving the surname reads as clean — the exact partial-redaction defect it was written
     to catch.
  6. Three of `run_once`'s four integrity gates on the headline number are still deletable
     with a green suite.
  7. `docs/testing.html` publishes the safety gate's freshness check as tested; it has no test.
  8. The kill switch's eval pass-rate and coverage floors are never executed by any test.
- **Three of the three mechanizations built in pass 16 are themselves defective** (findings 4,
  5, and the MEDIUM on `mutate.py`'s circular catalogue check, which "certifies a tree with
  four safety controls physically removed"). Treat the mutation harness's green result as
  unproven until that is fixed.
- **The offline socket tripwire is bypassable** by a bytes host, `connect_ex`, and DNS —
  pass 17 got a real outbound connection off the machine (MEDIUM).
- `dashboard/data.js` still publishes the post-repair recomputation as the signal Act
  consumed, contradicting the `manifest.json` fix (MEDIUM). The export was not re-run.
- Pass 16's remaining **LOW** items.
- The four non-committed retained manifests carry pre-repair `offer_effectiveness` **and** an
  `intervention_signal_id` that does not resolve. Both are disclosed in
  `dashboard/manifests/README.md`; neither is fixable, because the databases that would make
  them meaningful were discarded by `--median`. Inventing values would be fabricating
  provenance.
- `keel.db` is **untracked**. Artifact-consistency tests skip when it is absent (fresh clone,
  CI, mutation harness's copied tree). The underlying logic is covered portably on temp DBs.
