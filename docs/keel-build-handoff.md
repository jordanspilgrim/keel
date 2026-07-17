# Keel — Build Handoff for Claude Code

*Prepared 17 July 2026. This is a standalone brief: it carries everything a fresh Claude Code session needs to build the POC without the originating conversation. Read it top to bottom once before writing code.*

---

## 0. TL;DR — what you're building

Build **Keel**, a proof-of-concept **customer-retention agent platform** on the OpenAI API. It is one product made of three tightly-coupled layers that form a closed loop:

1. **Act** — a *cancellation-saver agent* that handles a customer trying to cancel, detects churn intent, and makes a bounded retention offer within policy.
2. **Measure** — an *evaluation harness* that grades every conversation (resolution, policy adherence, offer appropriateness, tone, hallucination, plus a fairness slice), with a hand-labeled golden set for regression.
3. **Learn** — a *voice-of-customer analytics* layer that clusters graded conversations into churn themes and ranked roadmap signals.

Wrapped around all three: **safety guardrails**, **EU-AI-Act + GDPR compliance by design**, a **visualization dashboard**, and a **unit-economics model**.

**Why it exists:** it's a portfolio-grade POC demonstrating platform-PM judgment for customer-facing AI-agent roles (Sierra, Decagon, Cresta). The thing that must land is *the flywheel visibly turning*: an analytics insight → a new offer/policy → a measured lift on the next batch.

**Companion reference files (delivered alongside this doc, treat as source of truth for intent):**
- `retention-flywheel-plan.md` — the full plan (architecture, milestones, metrics, safety, compliance, economics). **Read its §0 principles and §3 architecture before starting.**
- `keel-dashboard.html` — a working, validated visualization mockup. Reuse it as the dashboard, or reimplement; the data contract it expects is your target.
- `keel-economics.html` — an interactive unit-economics calculator encoding the cost/value model in §8 below.

---

## 1. Operating principles (these govern every decision)

1. **Simple to use and manage** — one person can run it; no vendor needed to change config.
2. **Biased to the next best action** — always move toward resolution.
3. **Calibrated autonomy** — freedom to act scales with reversibility × confidence. Low-risk/reversible/high-confidence → act; high-stakes/irreversible/low-confidence → propose + human confirm. This single rule reconciles "autonomous" with "human-in-the-loop."
4. **Legible, not just autonomous** — every decision inspectable and explainable in plain language.
5. **Fails safe** — under uncertainty, defer/escalate rather than guess; prefer reversible actions.
6. **Measured, and self-improving** — grades its own work; ROI is proven by the loop, not asserted.
7. **Economic from day one, outcome-honest** — ROI-positive on the first use case; optimize a metric it can't game (margin-adjusted save rate, not raw).
8. **Fast to first value** — days to a live win, not months.
9. **Meets the stack where it is** — composable with existing systems (mocked here).

When a design choice is ambiguous, pick the option that best satisfies these in priority order.

---

## 2. Target architecture

Five small services + one datastore. Everything must run locally on a laptop with only an `OPENAI_API_KEY`.

- **Conversation runtime (agent).** OpenAI Responses API + tool calling. Tools are deterministic Python functions: `get_customer`, `get_subscription`, `get_usage`, `offer_discount`, `offer_pause`, `downgrade_plan`, `escalate_to_human`. Final turn emits a **structured disposition** via JSON schema: `{intent, churn_reason, offer_made, offer_accepted, outcome, confidence}`.
- **Policy / guardrail layer.** A deterministic wrapper *around* the model (see §5). The model proposes; this layer disposes. Enforces authorization limits, eligibility, and human-in-the-loop.
- **Eval service.** LLM-as-judge (cheaper "mini" model) scoring each conversation on a rubric → structured verdict `{per_dimension_scores, verdict, rationale}`; plus a golden set run on every prompt change.
- **Analytics service.** `text-embedding-3-small` over conversation summaries → clustering (sklearn KMeans is fine to start) → per-cluster LLM summarization → theme cards `{label, summary, size, save_rate, avg_margin_cost, example_ids}` → ranked signals.
- **Dashboard + synthetic engine.** A synthetic-customer generator produces personas + churn reasons (also the adversarial test set). Dashboard reads the DB and renders KPIs, trend, churn drivers, offer scatter, safety/compliance panel (reuse `keel-dashboard.html`'s structure).

---

## 3. Tech stack & conventions

- **Language:** Python 3.11+.
- **OpenAI SDK:** official `openai` Python package. Use the Responses API, tool/function calling, Structured Outputs (JSON schema), and `text-embedding-3-small`. Resolve concrete model IDs at build time to the latest available flagship + mini + embedding models; keep them in one `config.py` constant block so they're swappable.
- **Datastore:** SQLite (stdlib `sqlite3` or SQLModel). One file, `keel.db`.
- **Analytics:** `scikit-learn` (KMeans), `numpy`.
- **Dashboard:** reuse the provided single-file HTML, fed by a small JSON export endpoint/script; or Streamlit if you prefer live Python. Do not add a heavy frontend framework.
- **Tests/evals:** `pytest`. The golden set lives as JSON fixtures.
- **Secrets:** `.env` with `OPENAI_API_KEY`; provide `.env.example`. Never commit keys.
- **Keep dependencies minimal.** `requirements.txt` should be short: `openai`, `scikit-learn`, `numpy`, `python-dotenv`, `pytest`, (optional) `streamlit`.
- **Determinism where it matters:** fix random seeds in the synthetic generator and clustering so demo runs are reproducible.

Suggested repo layout:

```
keel/
  README.md
  requirements.txt
  .env.example
  config.py               # model IDs, policy limits, thresholds
  db.py                   # schema + helpers
  synth.py                # synthetic customers + churn scenarios (seeded)
  agent/
    runtime.py            # Responses API loop + tools + disposition
    tools.py              # deterministic tool implementations (mock backends)
    policy.py             # deterministic guardrail/authorization layer
    guardrails.py         # input/output guardrails (jailbreak, scope, PII, grounding)
    disclosure.py         # mandatory AI-disclosure turn
  evals/
    judge.py             # LLM-as-judge rubric + fairness slice
    golden/*.json        # hand-labeled regression set
    run_evals.py
  analytics/
    embed.py
    cluster.py
    themes.py            # cluster summarization + ranked signals
  dashboard/
    export.py            # DB -> dashboard JSON
    index.html           # reuse keel-dashboard.html, wired to real data
  economics.py            # cost/value model (mirror keel-economics.html)
  run_demo.py             # end-to-end: generate -> converse -> grade -> analyze -> export
```

---

## 4. Build order (phased — each phase ends in a demoable, testable state)

Build in this order; do not skip the guardrail/compliance phase before evals.

**Phase 0 — Scaffold (½ day).** Repo, `requirements.txt`, `.env.example`, `config.py`, `db.py` schema (incl. `guardrail_events`, `audit_log`), and seeded `synth.py`.
*Acceptance:* `python synth.py` populates the DB with N synthetic customers + subscriptions + churn scenarios, reproducibly.

**Phase 1 — Cancellation-saver agent (2–3 days).** Responses-API loop with the tools, the **mandatory AI-disclosure turn from the first message**, structured disposition, and the deterministic policy layer.
*Acceptance:* a scripted run saves an eligible customer with a pause offer and correctly lets an ineligible one churn; disclosure present in every transcript; no offer ever exceeds policy limits.

**Phase 2 — Guardrails & compliance (2 days).** Input guardrails (jailbreak, off-scope, PII detection/redaction), action guardrails (authorization limits, **human-in-the-loop for consequential/irreversible actions**), output guardrails (grounding, promise-check, tone). `audit_log` + `guardrail_events` populated.
*Acceptance:* a jailbreak attempt is blocked; an off-scope question is bounded; an over-limit discount is rejected; a refund-denial routes to human approval; PII is redacted before it is logged or embedded.

**Phase 3 — Eval harness (2 days).** LLM-judge rubric (include a fairness slice across a synthetic demographic attribute) + golden-set regression runner + judge-vs-human agreement report.
*Acceptance:* every conversation is auto-scored; deliberately breaking the agent prompt makes the golden set fail; agreement metric printed.

**Phase 4 — VoC analytics (2 days).** De-identified embed → cluster → theme cards → ranked roadmap signals.
*Acceptance:* produces "top 3 churn drivers" and a comparison like "20%-off saves ~8pp more than pause but costs ~3× the margin," from clustered data only.

**Phase 5 — Close the loop (1–2 days).** Wire the dashboard to real DB exports; then *act on one analytics insight* (add an offer the analytics recommends) and show the measured lift on the next batch.
*Acceptance:* `run_demo.py` executes the full flywheel end-to-end and the dashboard reflects the lift. **This is the money demo.**

**Phase 6 — Stretch.** Adversarial red-team suite from the synthetic engine; A/B offer testing; a "propose a policy change" agent that drafts the next guardrail from analytics.

---

## 5. Safety & compliance — non-negotiables

Design these in from Phase 1–2, not bolted on:

- **AI disclosure (EU AI Act Art. 50, binding 2 Aug 2026):** the agent states it's an AI at conversation start; enforce as an input-side guardrail that cannot be skipped.
- **Human-in-the-loop (GDPR Art. 22):** any action with a significant/legal effect (refund denial, contract change, anything eligibility/credit/health-related) requires human approval. Reversible low-stakes actions may be autonomous (calibrated autonomy).
- **Data minimization:** redact PII before logging and before embedding; cluster on de-identified summaries only. Set retention limits.
- **Traceability:** `audit_log` records every decision + guardrail event.
- **Fairness monitoring:** the eval rubric includes a fairness slice (bias monitoring now applies to all AI systems under the Digital Omnibus).
- **Risk-tier note:** a retention agent is *limited-risk* (transparency only) until it touches credit/insurance/health eligibility, at which point it becomes *high-risk* (full obligations, deferred to 2 Dec 2027). Keep the POC on the limited-risk side; note the boundary in the README.

Treat this section as regulatory *architecture*, not legal advice; a real deployment needs counsel to confirm the risk-tier call.

---

## 6. Economics — what to instrument

Mirror `economics.py` on `keel-economics.html`. The key finding to preserve and surface: **~97% of blended cost-to-serve is human escalation; the entire AI stack (agent + grading every conversation + guardrails) is ≈3¢/conversation.** Consequences to bake in:

- The optimization lever is **containment** (safely reducing escalation), not model choice or token-shaving.
- **Grade 100% of conversations** — evaluation is nearly free; never sample to "save money."
- North-star metric is **margin-adjusted save rate**, not raw save rate.
- Track: cost/conversation, cost/save, escalation rate, margin-adjusted save rate, eval pass rate, guardrail catch rate.

Seed prices from current OpenAI rates (flagship ≈ \$1.25/\$10 per 1M in/out; mini ≈ \$0.25/\$2; `text-embedding-3-small` ≈ \$0.02/1M) but keep them in `config.py` as editable constants.

---

## 7. Data model (SQLite tables)

- `customers(id, name, segment, tenure_months, arpu, demographic_attr, …)`
- `subscriptions(id, customer_id, plan, price, status, …)`
- `conversations(id, customer_id, transcript_json, disposition_json, offer_made, outcome, created_at)`
- `evals(id, conversation_id, scores_json, verdict, rationale, fairness_flag)`
- `guardrail_events(id, conversation_id, type, action, detail, created_at)`
- `audit_log(id, conversation_id, actor, decision, reason, created_at)`
- `themes(id, label, summary, size, save_rate, avg_margin_cost, example_ids_json)`
- `signals(id, theme_id, recommendation, priority_score)`

---

## 8. Scope, assumptions, and open decisions

**In scope:** a locally-runnable, single-tenant POC on synthetic data that demonstrates the full flywheel end-to-end with safety, compliance, evals, analytics, dashboard, and an economics model.

**Out of scope / assumptions:** no real customer data or real PII (all synthetic); mock tool backends (no real CRM/billing); not production-hardened; no auth/multi-tenant; no real payment actions.

**Open decisions for you (the builder) — make a reasonable call and note it in the README:**
- Concrete model IDs (use latest available flagship/mini/embedding).
- Dashboard: reuse the provided HTML vs. Streamlit.
- Clustering: KMeans with a fixed k vs. auto-k; start simple.
- Synthetic dataset size (default ~200 customers for fast demo runs).

---

## 9. Start here

1. Read `retention-flywheel-plan.md` §0 (principles) and §3 (architecture), and skim `keel-dashboard.html` / `keel-economics.html` for the data and economics contracts.
2. Scaffold the repo per §3 layout; write `requirements.txt`, `.env.example`, `config.py`, `db.py`.
3. Build `synth.py` (seeded) and confirm Phase 0 acceptance.
4. Proceed through Phases 1→5 in order, meeting each acceptance criterion before advancing.
5. Keep a running `README.md` documenting decisions, how to run `run_demo.py`, and the limited-risk compliance posture.

The definition of done for the POC: `python run_demo.py` generates synthetic cancellations, the agent handles them under guardrails with disclosure and audit, every conversation is graded, analytics produce themes + signals, one signal is acted on, and the dashboard shows the resulting lift — the flywheel turning, once, end to end.
