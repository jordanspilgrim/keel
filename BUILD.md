# Keel — Build Checklist (source of truth)

Phased build; each phase ends in a demoable, testable state. Do not skip the
guardrail/compliance phase before evals. Full intent: `docs/keel-build-handoff.md` §4.

Base: standalone repo, Python 3.11+ (built on 3.14.6), `main` branch.
Model IDs verified available 2026-07-17: `gpt-5`, `gpt-5-mini`, `text-embedding-3-small`.

| Phase | State | Deliverables | Acceptance gate |
|---|---|---|---|
| **0 — Scaffold + synth** | ✅ **done** | `config.py`, `db.py`, `synth.py`, `economics.py`, `.env.example`, `requirements.txt`, dashboard mockup, stubs | `python synth.py` seeds keel.db reproducibly (200 cust / 200 sub / 211 scenarios; identical SHA on re-run) ✓; `python economics.py` reproduces the $1.28/97% headline ✓ |
| **1 — Cancellation-saver agent** | ⬜ next | `agent/runtime.py` loop, `agent/tools.py`, `agent/disclosure.py` (done), structured disposition, `agent/policy.py` wiring | scripted run saves an eligible customer with a pause offer, lets an ineligible one churn; disclosure in every transcript; no offer exceeds policy limits |
| **2 — Guardrails & compliance** | ⬜ | `agent/guardrails.py` (input: jailbreak/scope/PII; output: grounding/promise/tone), `agent/policy.py` (authorization + human-in-the-loop), `audit_log` + `guardrail_events` populated | jailbreak blocked; off-scope bounded; over-limit discount rejected; refund-denial → human; PII redacted before log/embed |
| **3 — Eval harness** | ⬜ | `evals/judge.py` rubric + fairness slice, `evals/run_evals.py`, `evals/golden/*.json` | every conversation auto-scored; breaking the agent prompt fails the golden set; judge-vs-human agreement printed |
| **4 — VoC analytics** | ⬜ | `analytics/embed.py`, `analytics/cluster.py`, `analytics/themes.py` | top-3 churn drivers + "20%-off saves ~8pp more than pause but costs ~3× margin" from clustered data only |
| **5 — Close the loop** | ⬜ | `dashboard/export.py` → `data.json` wired to `dashboard/index.html`; `run_demo.py` acts on one signal, shows the lift | `python run_demo.py` runs the full flywheel end-to-end; dashboard reflects the lift — **the money demo** |
| **6 — Stretch** | ⬜ | adversarial red-team suite (synth already seeds 11 probes), A/B offer testing, "propose a policy change" agent | — |

## Phase 0 — verified

- `synth.py` → 200 customers, 200 subscriptions, 211 scenarios (200 churn: 166 eligible / 34 ineligible; 11 adversarial: 4 jailbreak / 4 off-scope / 3 PII). Byte-identical across two runs.
- `economics.py` → cost/conv $1.28 (97.4% human escalation), AI stack $0.034, cost/save $3.21, margin 79%, break-even 8.6%, customer return 17.3×. Matches plan §9.
- `agent/disclosure.py` implemented (Art. 50 disclosure + presence check).

## Notes

- Phase 1 onward needs a working `OPENAI_API_KEY` in `.env` (validated 2026-07-17).
- `scenarios` table is a deliberate deviation from handoff §7 — see README.
- Keep the POC limited-risk (no credit/insurance/health eligibility).
