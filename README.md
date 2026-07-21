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
python -m pytest tests/ -q      # 54 tests (policy, guardrails, enforcement, live session, server)

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
- **Dataset size** — 200 synthetic customers (fast demo runs).
- **Deviation from handoff §7** — added a `scenarios` table. §7 gives synth no home for the churn scenario that drives a conversation, nor the adversarial (jailbreak/off-scope/PII) probes that feed the guardrail red-team. One clean table holds both.

## Compliance posture — limited-risk by design

This POC is a **limited-risk** system under the EU AI Act: a retention/CX agent whose only binding obligation is transparency. It becomes **high-risk** only if pointed at an Annex III task (creditworthiness, insurance pricing/eligibility, access to essential services) — the heavy obligations for which are deferred to 2 Dec 2027. **The POC stays on the limited-risk side.**

Baked in, not bolted on:

| Obligation | Where |
|---|---|
| AI Act Art. 50 — AI disclosure (binding 2 Aug 2026) | `agent/disclosure.py`, enforced as an input-side guardrail |
| GDPR Art. 22 — human review of significant decisions | `agent/policy.py` human-in-the-loop for `CONSEQUENTIAL_TOOLS` |
| Data minimization / purpose limitation | `agent/guardrails.py` PII redaction before log + embed; cluster on de-identified summaries |
| Bias monitoring (all systems, per Digital Omnibus) | fairness slice in `evals/judge.py` |
| Traceability / record-keeping | `audit_log` + `guardrail_events` in `db.py` |

*This is regulatory architecture, not legal advice; a real deployment needs counsel to confirm the risk-tier call.*

## Status

All six phases are built and verified, then hardened through two independent review passes (see [`BUILD.md`](BUILD.md) → *Independent review & remediation* and *Second independent review*). The flywheel demo (`python run_demo.py`) SELECTS the treated segment from the baseline analytics signal (highest loss impact — it lands on the price-sensitive segment because that's where the discount lever helps) and produces a real, measured lift there. Conversations are LLM-driven and the treated segment is small (n≈8), so the baseline draw — and therefore the lift magnitude — vary run to run: across runs the treated segment baselines anywhere from ~38% to ~62% and lands consistently around **~75%** after the act (representative run: **38% → 75%, +38pp**; a lower-variance earlier run: 62% → 75%, +12pp). Overall lift is reported as context (+22pp this run). The demo requires a matched paired cohort and a strictly-positive treated-segment lift, and writes full provenance to `dashboard/manifest.json`. Eval pass rate is a strict, trace-aware judge (varies ~75–94% by run); guardrail catch rate 100% (11/11 seeded probes), 100% compliance coverage.
