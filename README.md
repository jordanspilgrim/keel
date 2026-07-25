# Keel — Retention Flywheel

*An AI retention agent that saves cancellations, grades itself on every conversation, tells the product team what to build next — and is safe and EU-AI-Act-compliant by design.*

Keel is a local, single-tenant proof-of-concept on the OpenAI API. It's one product made of three tightly-coupled layers that form a closed loop:

- **Act** — a cancellation-saver agent that handles a customer trying to cancel and makes a bounded retention offer within policy.
- **Measure** — an eval harness that grades every conversation (resolution, policy adherence, offer fit, tone, hallucination, plus a fairness slice), with a golden set for regression.
- **Learn** — voice-of-customer analytics that clusters graded conversations into churn themes and ranked roadmap signals.

Wrapped around all three: safety guardrails, EU-AI-Act + GDPR compliance by design, a visualization dashboard, and a unit-economics model. The thing that must land is *the flywheel visibly turning*: an analytics insight → a new offer/policy → a measured lift on the next batch.

Full intent lives in [`docs/retention-flywheel-plan.md`](docs/retention-flywheel-plan.md) and [`docs/keel-build-handoff.md`](docs/keel-build-handoff.md).

**Browsable docs** (open in a browser): [`docs/index.html`](docs/index.html) — plain-English overview + the flywheel · [`docs/how-it-works.html`](docs/how-it-works.html) — architecture, agent loop, guardrails, compliance · [`docs/testing.html`](docs/testing.html) — run and test each phase · plus the [dashboard](docs/keel-dashboard.html) and [economics calculator](docs/economics-calculator.html) mockups.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then paste your OPENAI_API_KEY into .env

# no API key needed:
python synth.py                 # seeds keel.db (reproducible)
python economics.py             # prints the unit-economics model
python -m pytest tests/ -q      # 194 tests (policy, guardrails, enforcement, ledger/envelope, live session, money-demo, server)

# the money demo — the whole flywheel end to end (needs OPENAI_API_KEY):
python run_demo.py              # baseline → learn → act → re-measure → export
open dashboard/index.html       # renders the measured lift
```

## Architecture

Five small services + one SQLite datastore (`keel.db`), all runnable on a laptop:

| Service | Module(s) | Phase |
|---|---|---|
| Conversation runtime (agent) | `agent/runtime.py`, `agent/tools.py`, `agent/disclosure.py` | 1 |
| Policy / guardrail layer | `agent/policy.py`, `agent/guardrails.py` | 2 |
| Eval service | `evals/judge.py`, `evals/run_evals.py`, `evals/golden/` | 3 |
| Analytics service | `analytics/embed.py`, `analytics/cluster.py`, `analytics/themes.py` | 4 |
| Dashboard + synthetic engine | `dashboard/`, `synth.py`, `economics.py`, `run_demo.py` | 0 / 5 |

Data model and schema: `db.py` (handoff §7). Build order and acceptance gates: [`BUILD.md`](BUILD.md).

## Builder decisions (handoff §8)

- **Model IDs** — `gpt-5` (flagship), `gpt-5-mini` (judge/classifiers), `text-embedding-3-small`. Verified available against the live model list on 2026-07-17. All in `config.py`, env-overridable. (`gpt-5-nano` is available if an even cheaper triage classifier is wanted.)
- **Dashboard** — reuse the provided static `dashboard/index.html`, fed by `dashboard/export.py` → `dashboard/data.json` (no heavy frontend framework).
- **Clustering** — KMeans with fixed `k=5` and fixed `random_state` for reproducibility; auto-k is a later refinement.
- **Dataset size** — 200 synthetic customers by default (tests/economics); the money demo seeds a larger world (`DEMO_SYNTH_N`) so the treated cohort is **n=60** (one customer ≈ 1.7pp), which bounds per-customer leverage — run-to-run variance is then handled by the pre-registered median-of-k, not by n alone.
- **Deviation from handoff §7** — added a `scenarios` table. §7 gives synth no home for the churn scenario that drives a conversation, nor the adversarial (jailbreak/off-scope/PII) probes that feed the guardrail red-team. One clean table holds both.

## Compliance posture — limited-risk by design

This POC is a **limited-risk** system under the EU AI Act: a retention/CX agent whose only binding obligation is transparency. It becomes **high-risk** only if pointed at an Annex III task (creditworthiness, insurance pricing/eligibility, access to essential services) — the heavy obligations for which are deferred to 2 Dec 2027. **The POC stays on the limited-risk side.**

Baked in, not bolted on:

| Obligation | Where |
|---|---|
| AI Act Art. 50 — AI disclosure (binding 2 Aug 2026) | `agent/disclosure.py` — prepended to every conversation **and enforced at persist**: `has_disclosure()` gates `persist_conversation`, so a non-disclosing transcript can never reach the store |
| GDPR Art. 22 — human review of significant decisions | `agent/policy.py` human-in-the-loop for `CONSEQUENTIAL_TOOLS` |
| Data minimization (a defined sensitive-pattern set — card/SSN/email/DOB/phone + keywords + heuristic self-identified names & street addresses) | `agent/guardrails.py` redaction before log + embed; cluster on de-identified summaries |
| Bias monitoring (all systems, per Digital Omnibus) | an *observational* outcome-parity slice (`evals/run_evals.py`) **plus** a real counterfactual **agent-treatment fairness harness** (`evals/agent_fairness.py`) |
| Traceability / record-keeping | `audit_log` + `guardrail_events` in `db.py` |

*This is regulatory architecture, not legal advice; a real deployment needs counsel to confirm the risk-tier call.*

## Status

All six phases are built and verified, then hardened through **nine independent review passes, a proactive self-hardening pass, and an external independent code + design review** (numbered through the eleventh in [`BUILD.md`](BUILD.md) → the *review* sections; the fifth slot was proactive self-hardening, not an outside review). The core safety architecture:

- **The customer-facing reply is 100% server-authored.** The model never writes a customer-facing sentence — it emits *structured intent* (an acknowledgement chosen from a fixed set, an offer as kind+terms, account facts by reference) and the server renders every sentence from validated data. There is no free-form channel in which the model could state an account fact, a capability, a refund, or a cancellation it isn't entitled to; the hand-off message is server-templated on the same basis, and a let-go says a teammate will process the cancellation (no billing-period fact the POC can't back).
- **A single typed offer ledger** (`authorized → presented → accepted`) is the source of truth for outcome, cooldown, economics, and the eval envelope. Multiple offers may be *authorized* as candidates; exactly one is ever *presented*. The agent's final turn is a structured response contract validated deterministically against that ledger.
- **The eval is a content-hashed spec** whose version is derived from the actual prompt-formatter source (not a hand-maintained marker), stamped identically on live and batch grades, with one grade per conversation per spec enforced by a unique index. Every eval metric — dashboard, `/api/metrics`, kill switch, explorer — counts only the current spec, so a retained history of superseded grades can never push a rate above 100%.
- **Resolution is idempotent with rollback**: the writes are one transaction that rolls back on failure, and a durable, DB-unique resolution key makes a retry return the existing record even across a restart.
- **The flywheel runs under an immutable `run_id` lineage**: both arms run from a byte-identical *restored world snapshot* (recorded as `starting_state_sha` in the manifest), so eligibility is a held constant and the only variables that change between arms are the discount policy and the agent playbook.

The judge treats the graded conversation as data, not instructions (a golden fixture embeds a "give all 5s" attack and is still scored fail); the golden set carries per-dimension human scores with judge calibration gated at mean error ≤ 1 point; the seeded red-team is **14 probes** (6 jailbreak incl. paraphrases + 4 off-scope + 4 PII incl. name/address); economics/export formulas and a real two-thread resolve race have regression tests. **194 offline tests, no network** (a few seconds; timing varies by machine).

**The money demo** (`python run_demo.py`, or `--median --k=5` for the pre-registered estimate) runs the closed loop end-to-end under a paired, snapshot-controlled harness: baseline → a structured intervention signal selects the highest-loss lever-compatible segment (it lands on price-sensitive) → enable the discount lever + a lead-with-discount playbook → re-measure the **same** cohort from a byte-identical *restored world* (`starting_state_sha` recorded per arm, so eligibility is held constant). **How the segment is actually chosen — stated plainly:** `rank_segments` computes loss impact (`n × miss-rate`) by grouping on the scenario's **seeded `churn_reason` label**, not on the embedding/KMeans clusters. The clustering pipeline runs and produces the VoC theme cards and the dashboard's ranked signals, but it does **not** drive the intervention; removing it would leave the selected segment and the reported lift unchanged. In a real deployment the segment label would have to come from the derived clusters (or a CRM field), not from an answer key the synth planted — this POC keeps the two separate so the lift measurement stays unambiguous. **Measure gates Learn:** the baseline arm is graded *before* analytics, and the intervention is chosen only from conversations that passed the current eval spec (69 of 80 baseline conversations were eval-eligible in the committed run) — an ungraded or failed conversation can't steer the flywheel. **Honest statistical read:** a single run is high-variance because both arms are independently LLM-simulated, so the headline is a **pre-registered median of k=5 runs** (`--median`; fixed seed → identical world, so what varies is only the LLM draw; k fixed in advance, every run counted, the committed number is the median and never the max). Result on the committed build: the price-sensitive segment's save-rate lift had a **median of +23.3pp, range [+10.0, +25.0]pp across the 5 runs — all five positive** (values `[10.0, 25.0, 23.3, 15.0, 25.0]`; margin-adjusted median **+18.7pp**; overall-cohort median **+17.5pp**); eval pass held at a **median 86% (range 81–90%)**. **Read that number with the variance in mind:** an earlier pre-registered median-of-5, on the immediately preceding build, came out at **+11.7pp (range [+5.0, +18.3])**. Two honest median-of-5 estimates ~12pp apart is the real finding — median-of-k *narrows* the run-to-run spread of an LLM-simulated paired demo but does not collapse it to a point estimate. The defensible claim is **"a consistently positive treated-segment lift in the roughly +10 to +25pp band,"** not any single headline; both distributions are retained under `dashboard/manifests/`. The committed median-lift run (`run-20260725T002018`) moved the segment **5% → 28%** and is the one rendered on the dashboard (the live Explorer/API read the same run) — note its baseline arm (5%) was itself a low draw, which is why its lift sits at the median. The *median-of-k guarantee applies to the headline segment lift*; that run's own margin-adjusted figure is **+18.7pp**. Eval coverage/pass on the dashboard spans **both arms** (100.0% coverage / 85.6% pass there) while the manifest's figure is **after-arm only** (100.0% / 85.0%) — different populations, so the two are labeled separately rather than quoted interchangeably; the full k=5 distribution (run ids, per-metric medians, ranges) is in `dashboard/demo_aggregate.json`, each individual run's manifest retained under `dashboard/manifests/`. Fairness is monitored two ways: an **observational** outcome-parity gap (median **0.074** here — noise, since the agent never sees the attribute) and a real counterfactual **agent-treatment fairness harness** (`evals/agent_fairness.py`). **Scope:** two variables change together (discount policy + playbook) and there is no randomized holdout, so this is a paired *demonstration* of the flywheel on the treated segment — not an isolated causal estimate. The demo requires a matched paired cohort from an identical starting-state hash, a lever-compatible signal loaded by id, and a strictly-positive treated-segment lift to declare success. Guardrail catch rate 100% (14/14 seeded probes), 100% AI-disclosure coverage.
