# Keel — Build Checklist (source of truth)

Phased build; each phase ends in a demoable, testable state. Do not skip the
guardrail/compliance phase before evals. Full intent: `docs/keel-build-handoff.md` §4.

Base: standalone repo, Python 3.11+ (built on 3.14.6), `main` branch.
Model IDs verified available 2026-07-17: `gpt-5`, `gpt-5-mini`, `text-embedding-3-small`.

| Phase | State | Deliverables | Acceptance gate |
|---|---|---|---|
| **0 — Scaffold + synth** | ✅ **done** | `config.py`, `db.py`, `synth.py`, `economics.py`, `.env.example`, `requirements.txt`, dashboard mockup, stubs | `python synth.py` seeds keel.db reproducibly (200 cust / 200 sub / 211 scenarios; identical SHA on re-run) ✓; `python economics.py` reproduces the $1.28/97% headline ✓ |
| **1 — Cancellation-saver agent** | ✅ **done** | `agent/runtime.py` Responses-API loop, `agent/tools.py`, `agent/policy.py`, `agent/disclosure.py`, `sim.py` (customer simulator), `llm.py`, structured disposition | `python -m scripts.phase1_accept`: eligible → **saved** (3-mo pause), ineligible → **lost** (cooldown-rejected save offer, graceful churn); disclosure in every transcript; no offer exceeds limits ✓; 10 unit tests pass |
| **2 — Guardrails & compliance** | ✅ **done** | `agent/guardrails.py` (input: PII redact / jailbreak / scope; output: tone / promise / grounding), policy human-in-the-loop (+`deny_refund` tool), `guardrail_events` populated, PII redacted before store | `python -m scripts.phase2_accept`: **100% catch rate (11/11)** — jailbreak 4/4 blocked, off-scope 4/4 bounded, PII 3/3 redacted, over-limit 40%→capped 20%, refund→human ✓; 79 tests pass |
| **3 — Eval harness** | ✅ **done** | `evals/judge.py` (5-dim rubric + fairness flag, fed the persisted eval envelope), `evals/run_evals.py` (grade_all + run_golden + `build_judge_input`), 9 golden fixtures, `batch.py` concurrent runner | `python -m scripts.phase3_accept` (representative run): 12/12 graded (eval pass ~92%), golden agreement **100% (9/9)** at the 80% floor, paired-fairness consistent (per-dimension), a known-bad conversation → **fail** (policy_adherence 1, hallucination 1); fairness slice by group reported |
| **4 — VoC analytics** | ✅ **done** | `analytics/embed.py` (batched), `analytics/cluster.py` (KMeans), `analytics/themes.py` (theme cards + offer effectiveness + ranked signals), `economics.margin_cost` | `python -m scripts.phase4_accept`: 30-conv batch → 5 themes, top-3 drivers, ranked signals, offer comparison from clusters ✓ |
| **5 — Close the loop** | ✅ **done** | `dashboard/export.py` → `data.js` wired to a data-driven `dashboard/index.html`; `run_demo.py` consumes a structured intervention signal (persisted + loaded by id), runs baseline → learn → act → re-measure over an **n=20** treated cohort (stable estimate), requires a paired cohort + lever-compatible signal + strictly-positive treated-segment lift, writes `manifest.json` (+ dated copies in `dashboard/manifests/`) | `python run_demo.py` (single committed run, one `run_id` lineage): the signal selected the price-sensitive segment (worst lever-compatible loss); treated save rate **15% → 60% (+45pp)**, margin-adjusted 12% → 48%, overall +33pp context, eval pass 92% — **the flywheel turning**. Numbers vary run to run; only the committed run is claimed |
| **6 — Stretch** | ⬜ | adversarial red-team suite (synth already seeds 11 probes), A/B offer testing, "propose a policy change" agent | — |

## Phase 0 — verified

- `synth.py` → 200 customers, 200 subscriptions, 211 scenarios (200 churn: 166 eligible / 34 ineligible; 11 adversarial: 4 jailbreak / 4 off-scope / 3 PII). Byte-identical across two runs.
- `economics.py` → cost/conv $1.28 (97.4% human escalation), AI stack $0.034, cost/save $3.21, margin 79%, break-even 8.6%, customer return 17.3×. Matches plan §9.
- `agent/disclosure.py` implemented (Art. 50 disclosure + presence check).

## Phase 1 — verified

- `agent/runtime.py`: Responses-API tool loop (gpt-5, reasoning effort low) + a seeded customer simulator (`sim.py`, gpt-5-mini) drives a real negotiation to a saved/lost/escalated outcome. Structured-output disposition reconciled with the mechanically-known outcome (the loop is source of truth).
- `agent/tools.py`: 8 tools (incl. `deny_refund`); read tools execute directly, action tools routed through `policy.authorize`. `customer_id` is bound by the runtime (tool sandboxing) — the model can't name another account.
- `agent/policy.py`: deterministic authorization — discount cap 20%, pause cap 3mo, margin floor, save-offer cooldown (eligibility), consequential → human. 7 unit tests.
- Acceptance run: eligible → saved (pause), ineligible → lost. Conversations + dispositions + audit_log (disclosure + every policy decision) persisted.
- Two fidelity bugs found and fixed during verification (not papered over): (1) a visibly-accepted save was mis-logged `lost` (sim decision vs. prose mismatch); (2) "customer says yes to cancelling" was mis-counted as `saved` — a save now requires an accepted *retention offer*.
- New files beyond handoff §3 layout: `sim.py` (customer simulator, needed to drive conversations) and `llm.py` (shared client + Structured Outputs helper). Noted here and in README intent.

## Phase 2 — verified

- `agent/guardrails.py`: input pipeline (redaction of a DEFINED sensitive-pattern set — card/SSN/email/DOB/phone + keywords, NOT names/addresses — → deterministic jailbreak patterns → mini scope classifier) and output pipeline (Moderation tone + the deterministic response-contract validation). That pattern set is scrubbed before anything is stored or embedded; broader PII (names/addresses) is out of scope for the POC redactor.
- Wired into `runtime.py`: every user turn screened (scope classified at entry; PII+jailbreak every turn); every agent reply screened; `guardrail_events` written per conversation; a tone block escalates.
- Action guardrail: `deny_refund` added; consequential tools route to human (`human_review` event).
- Acceptance: 100% catch rate on the 11-probe red-team; over-limit discount capped to the 20% ceiling; refund routed to a human. 13 new unit tests (redaction, jailbreak patterns, promise/grounding) — 23 total.
- Design note: over-limit discounts are **capped** to the ceiling (never honored above it) rather than hard-rejected — this satisfies "the model can't exceed policy" while honoring the bias-to-next-best-action principle (offer the max allowed, don't just say no). Genuine rejection still fires on the margin floor and the save-offer cooldown.

## Phase 4 — verified

- `analytics/`: de-identified conversation summaries (customer's own redacted words) → batched `text-embedding-3-small` → KMeans(k=5) → per-cluster LLM theme cards (label/summary/size/save_rate/avg_margin_cost/example_ids) → ranked signals by volume × loss-impact. Themes + signals persisted.
- Offer effectiveness aggregation (save rate vs. margin cost per offer type) with `economics.margin_cost` — a 20% discount concedes the % of price monthly; a pause is a small goodwill fraction (≈3× cheaper).
- Fidelity fix during verification: the customer simulator was over-tuned toward rejection, producing an unbelievable ~7% overall save rate. Recalibrated to a *reasonable* customer (persuadable by a genuinely good, relevant offer) → realistic ~47% save rate with sensible per-theme variation (price-sensitive 75%, competitor-switch 22%). Not rigged — the sim just stopped being a brick wall. Prior gates (Phase 1/2) still hold: eligible saves more readily, ineligible still churns (no offer authorized under cooldown regardless of the sim).

## Phase 5 — verified (the money demo)

- `dashboard/export.py`: computes every dashboard view from the DB (KPIs incl. margin-adjusted save rate, before/after trend, clustered drivers, offer effectiveness, safety) → writes `dashboard/data.js` (`window.KEEL_DATA`). The dashboard loads it via `<script src>` (works on file://) and falls back to a mock if absent.
- `dashboard/index.html`: rewritten data-driven — same design language, now rendering real demo output.
- `run_demo.py`: the full flywheel on one identical seeded cohort — BASELINE (discounts disabled) → grade + cluster → a **structured intervention signal** (`themes.recommend_intervention`) selects the highest-loss segment *for which a lever exists* and surfaces any higher-loss segment it can't address → ACT (enable discounts + lead-with-discount playbook for the selected segment) → RE-MEASURE. The lift is measured on the **treated segment** where the act applies, over an **n=20** treated cohort (at n=8 a single customer swung it 12.5pp, so a one-run lift was noise). Single committed run (under one immutable `run_id`, no DB reset between phases): 15%→60% (+45pp), margin-adjusted 12%→48%, overall +33pp context, eval pass 92%. Conversations are LLM-driven so numbers vary run to run — only the committed run is claimed. The signal is persisted under the run_id and **loaded back by id** (durable Learn→Act lineage). Re-seeds between batches so cooldown state doesn't carry over; requires a matched paired cohort, a lever-compatible signal, and a strictly-positive treated-segment lift; writes `dashboard/manifest.json` plus a dated copy in `dashboard/manifests/` (cohort IDs, signal id + segment ranking, prompt/policy hashes, model IDs, eval coverage, lift).
- Definition of done met: `python run_demo.py` runs generate → converse → grade → analyze → act → re-measure → export, and the dashboard shows the lift.

## Keel Console — interactive web app (post-Phase-5)

A FastAPI app (`server.py` + `console/`) that turns Keel into something you can
operate, not just run — and draws the two integration seams explicitly.

- **Live chat (Wizard-of-Oz testing + demo):** you play the customer; the agent
  grounds against a real synthetic account and every turn streams a **step trace**
  (input screening → reasoning → tool calls → policy verdict → output check → reply)
  so the model latency is filled with *legibility* — you watch a jailbreak get
  blocked, a discount get capped, PII get redacted, in real time.
- **Explorer:** KPI cards with plain-English explanations + formulas (popovers),
  each click-through to the conversations behind the number — the
  `audit_log`/`evals`/`guardrail_events` made browsable (the "legible" principle).
- **Seams:** the web app is the first **Channel** adapter (a new channel maps its
  payloads onto `new_session`/`live_turn`); `agent/tools.py` is the **Backend**
  seam (synthetic reference impl → a real CRM/billing impl later). The agent core
  is untouched by either.
- **One code path:** `live_turn` reuses the exact batch screening/agent/policy/
  output logic via an optional `on_step` callback — one shared inner loop, so the
  two paths don't drift (they differ only in channel orchestration).

Run: `.venv/bin/uvicorn server:app --port 8500` (or via `.claude/launch.json` → `console`).

**Verified via tests + live smoke** (not a bug-free guarantee): 54 tests (live-session state
machine + FastAPI endpoints, mocked agent, no API); a live smoke
(`python -m scripts.console_smoke`) exercising the real agent end to end (happy
offer + step trace, jailbreak blocked, off-scope bounded, persistence); and a
real uvicorn boot serving over HTTP. Robustness: no shared SQLite connection
(per-request/thread), background-thread + polling turns (no leaked connections),
worker exceptions surfaced not hung, per-session busy-guard.

## Independent review &amp; remediation

The repo was put through an independent code + design review (Codex), which
correctly flagged that several claims were stronger than the code supported. The
findings below were addressed in this pass — though two later review passes (see
*Second* and *Third independent review*) found that some of these fixes were
shallower than claimed and drove the deeper architectural remediation:

- **Input guardrails** — batch and live now share ONE enforced decision
  (`_screen_input` → `_advance`): a jailbreak is blocked before the model on
  both paths (previously batch logged "blocked" but still forwarded it).
- **Output guardrails** — a promise/grounding/tone violation is now regenerated
  once and, failing that, fails closed to a human (previously logged-and-sent).
  Promise detector strengthened (spelled numbers, "half off", unauthorized-but-
  within-ceiling discounts).
- **Consequential actions** — `needs_human` is now a real state transition
  (stops the loop, hands off), not just an event label.
- **Live outcomes** — the server rejects a `saved` outcome with no accepted
  offer, resolution is idempotent, and every resolved conversation is graded
  (so "grade 100%" holds on the live path too).
- **Policy** — negative / non-finite discounts and non-positive pauses are
  rejected; accepted offers persist cooldown state.
- **Server** — atomic per-session busy-guard, idempotent resolve, connection
  creation inside `try`, per-call timeout, and eviction of old turns/sessions.
- **Evals** — trace-aware judge (sees the authorized-action trace), verdict
  derived mechanically from the scores, coverage reported honestly (a judge
  failure is a recorded coverage miss, never a silent drop); golden set expanded
  and its two over-claiming "pass" fixtures fixed.
- **Analytics** — realized margin cost only for *accepted* offers, pause cost
  scales with months.
- **Kill switch** — the config safety floors are now wired: new live sessions
  enter safe mode (disclosure + human) if eval health breaches; low-confidence
  dispositions are flagged.
- **Provenance** — `run_demo.py` writes `dashboard/manifest.json` (cohort IDs,
  prompt/policy hashes, model IDs, eval coverage, lift), requires a matched
  paired cohort and a strictly-positive lift, and is described as a synthetic
  paired demo (policy + playbook both change), not a causal estimate.
- **Tests** — 47 (up from 34), now exercising the *enforced* behavior (batch
  blocking, output fail-closed, needs-human transition, save-invariant, and the
  server's overlapping-turn / busy-guard rejection — a guard test, not a true
  multi-thread race) rather than mocking past it.

## Second independent review — bounded fixes &amp; honesty pass

A second review pass (Codex, AMBER verdict on commit 068622a) found real gaps
where enforcement or claims were still softer than described. These were addressed
as bounded fixes — but a third pass later showed several were still symptomatic
(e.g. the output check was strengthened here but remained lexical; the exact-terms
fix reached the live path but not batch grading), which drove the architectural
remediation in *Third independent review*:

- **Output check reconciles EXACT authorized terms, not a boolean** — `check_promise`
  now takes the exact authorized `{discount_pct, pause_months}` and flags a reply
  that promises a bigger discount, a longer/absent pause, or claims an action is
  already *applied* (the tools only propose). The runtime tracks `rec['authorized']`
  and the SYSTEM prompt now instructs offer/future language ("I can apply 20% off",
  not "I've applied it"). 7 new guardrail tests.
- **Scope classifier fails CLOSED** — an unavailable scope classifier now returns
  out-of-scope (bounded reply), not in-scope. Off-scope content enters model
  context only as a neutral marker, so a bounded off-topic message can't steer a
  later turn.
- **Kill switch actually reads the guardrail floor** — the red-team catch rate is
  persisted (`program_health`) when the red-team sweep runs and read by
  `safety.program_state`, which now gates safe-mode on `GUARDRAIL_CATCH_RATE_FLOOR`
  (previously the constant was defined but unused). It is NOT recomputed per poll
  (that would cost an LLM call per probe); absent = never run → not gated.
- **Max-hop fallback routes through handoff** — exhausting the tool-hop budget now
  sets `escalated`, logs a `max_hops` event, and hands off warmly, instead of
  returning a bare string.
- **Server duplicate-resolve is atomic** — a `_resolving` claim is taken under the
  lock so two concurrent resolves can't both run `resolve_session`.
- **Judge sees the exact authorized amounts** — the eval trace now includes the
  concrete terms (e.g. `offer_discount=capped(20% off)`), so the judge can catch a
  reply that over-promises versus what was authorized.
- **Golden pass fixtures reworded** — the four pass fixtures that asserted an action
  was already applied now use offer/future language; the broken-agent check uses
  the mechanically-derived verdict; `run_golden` adds a paired-fairness consistency
  check (identical conversation, different demographic group → same verdict).
- **Flywheel target is data-driven** — `run_demo` now SELECTS the treated segment
  from `themes.rank_segments` (highest loss impact in the baseline) rather than a
  hard-coded reason, and bails honestly if the selected segment isn't one the
  discount lever addresses.
- **One canonical metric definition** — `export.eval_metrics` / `compliance_coverage`
  are the single source of truth for eval pass-rate/coverage and transcript-verified
  compliance; both `/api/metrics` and the dashboard read them, so they can't diverge.
- **Simulator terminal reply recorded** — the customer's closing line is appended to
  the transcript before the loop breaks, so the persisted conversation is complete.
- **Batch honors the caller's DB** — `run_batch` workers open their own connection
  to the SAME database as the caller (derived from it), not the global default.
- **Tests** — 54 (up from 47); the new ones cover the exact-terms output check
  (over-ceiling, unauthorized, over-authorized, pause, completion-claim), scope
  fail-closed behavior, and the duplicate-resolve claim.

## Third independent review — architectural remediation

The third review (Codex, AMBER on commit `b975439`) found that the second round
had closed the easy cases but left three HIGH findings that shared ONE root
cause: offer/authorization state was scattered across four disagreeing places
(`offer_made` last-write, an `authorized` max-terms dict, a lossy `tool:action`
audit string, and the reply prose). The remediation replaces all of it with a
single typed **offer ledger** as the source of truth, and moves output safety off
regex onto a **structured response contract** validated against that ledger.

Remediation matrix (status is one of: **fixed** / **partial** / **accepted POC
boundary**). Every "fixed" links to the mechanism and an offline test.

| # | Finding | Status | Mechanism · test |
|---|---|---|---|
| H1 | Batch judge lost exact authorized terms | **fixed** | `evidence_json` envelope; `run_evals.build_judge_input` feeds BOTH batch (`grade_all`) and live (`_grade_and_store`) the SAME persisted offers-ledger + tool-facts · `test_authorized_terms_survive_persistence_and_reload` |
| H2 | Authorization was mutable metadata, not a state machine | **fixed** | `agent/offers.py` typed ledger (`authorized→presented→accepted`, one active via supersede); outcome/cooldown/economics derive from the **accepted presented** offer; a below-ceiling offer is costed at presented terms · `test_two_offer_calls_leave_one_active`, `test_offer_summary_prefers_presented_then_accepted` |
| H3 | Output control was lexical, with spelled/future bypasses | **fixed** (primary) | Structured response contract (`display_text`+`commitments`+`account_claims`) validated deterministically against the ledger + per-fact money grounding; regex demoted to a strengthened supplemental cross-check · `test_contract_validation_against_ledger`, `test_contract_grounding_rejects_invented_money`, `test_promise_catches_spelled_and_future_bypasses` |
| M1 | Terminal simulator reply bypassed redaction | **fixed** | `persist_conversation` redacts EVERY role · `test_persistence_redacts_all_roles` |
| M2 | Flywheel didn't consume the VoC signal layer | **partial** | Learn builds+persists a structured intervention signal (segment, lever, confidence, evidence, offer-effectiveness); it selects the highest-loss segment *for which a lever exists* and surfaces higher-loss segments it can't address (rather than bailing when a lever-less segment ranks first); Act consumes the signal id; the manifest records it. **Accepted boundary:** the segment ranking aggregates ground-truth `churn_reason`, not the free-text cluster labels |
| M3 | Safety gate accepted stale/missing health | **fixed** | health rows tagged with `GUARDRAIL_VERSION`; `program_state` gates on version-match + max-age + floor; missing = visible advisory (console usable), stale/mismatch/below-floor = safe mode · 4 safety tests |
| M4 | Paired fairness compared only binary verdict | **fixed** | `run_golden` retains per-dimension scores; symmetric pairs must match within ±1 per dimension + no fairness flag (scoped to pairs) |
| M5 | Eval not atomic/versioned with persistence | **partial** | `grade_all` replaces per-conversation (no global zero-coverage window, history not wiped); rows tagged with rubric+model version. **Accepted boundary:** eval creation is not yet in the same DB transaction as the conversation |
| M6 | Resolve connection created outside try/finally | **fixed** | `conn=None`, connect inside `try`, claim cleared in `finally` under the lock · `test_resolve_connection_failure_clears_claim` |
| M7 | Dashboard labels overstated coverage | **fixed** | dashboard renders `eval_coverage` from data; the compliance tile is relabeled "AI disclosure coverage (transcript-verified)" |
| L1 | In-memory batch fell back to global DB | **fixed** | `run_batch` rejects in-memory/unnamed connections · `test_batch_rejects_in_memory_connection` |
| L2 | Server shared state read/written off-lock | **fixed** | `turn_status` snapshots under the lock; the worker publishes terminal state under the lock |

**"One code path" is now also true for eval evidence:** batch and live grade from
the *same persisted envelope* (`build_judge_input`), closing the divergence the
third review noted. They still legitimately differ in customer (simulator vs
human), termination (simulator decision vs operator resolution), and the
session-creation safety gate — the accurate phrase remains "one shared enforced
turn core with separate channel, termination, and safety orchestration."

## Fourth independent review — closing the validator bypasses

The fourth review (Codex, AMBER on `1896868`) ran **executable probes** against the
response-contract validator and found it accepted several invalid contracts — the
differentiator was weaker than claimed. Each probe is now a regression test.

| # | Finding | Status | Mechanism · test |
|---|---|---|---|
| H1 | Contract self-attested; prose not bound to the ledger, `account_claims` unused, money laundered by substring, non-positive terms accepted | **fixed** | Every discount %/pause length in `display_text` is extracted (digits, "percent", spelled incl. hyphenated, fractions) and reconciled ≤ the committed terms; `account_claims` validated against the actual tool that ran + its values; money grounding matches only tool DOLLAR fields (price/credit/…) or a derived price, not any substring; `terms_within` rejects non-positive · `test_prose_offer_must_match_commitment`, `test_money_not_laundered_by_unrelated_number`, `test_account_claim_must_cite_a_real_tool_and_value`, `test_non_positive_committed_terms_rejected` |
| H2 | Hand-off bypassed output safety; escalation wasn't terminal | **fixed** | `_handoff_message` is screened (`_handoff_safe`: no offer, no invented fact) with a fixed fallback; escalation is a terminal state — `live_turn` and `/api/chat/turn` refuse further agent turns · `test_handoff_message_is_output_screened`, `test_escalation_is_terminal_no_more_agent_turns`, `test_escalated_session_rejects_further_turns` |
| M3 | Ledger never recorded rejection; fallback used ambiguous `active()` | **fixed** | `offers.mark_rejected` on sim-reject + live-lost; `offer_summary` still reflects an extended-then-rejected offer (cooldown/analytics); the ceiling fallback targets the failed commitment's kind · `test_rejection_transitions_presented_to_rejected` |
| M7 | Moderation outage logged "degraded" but still delivered | **fixed** | A degraded moderation result now fails the screen, so the reply falls through to deterministic safe text (ceiling template or hand-off), never tone-unverified model prose · `test_moderation_outage_does_not_deliver_model_prose` |
| M6 | Signal deleted before Act; range claim unbacked | **fixed** | The signal is persisted AFTER the pre-Act re-seed and Act loads it by id (durable Learn→Act lineage); docs report only the single committed run, not a range |
| M1 | "jailbreak blocked before the model" overstated | **fixed (docs)** | Reworded to "100% on the 11 seeded probes; paraphrases can evade regex — the deterministic policy layer is the backstop" |
| M2 | "PII scrubbed before anything logged/embedded" overstated | **fixed (docs)** | Reworded to "a defined sensitive-pattern set (card/SSN/email/DOB/phone + keywords), not names/addresses" |
| L2 | Stale counts / labels | **fixed (docs)** | 79 tests; single-run demo figure; run_demo header notes two variables; "known-bad conversation" not "broken agent" |

## Fifth pass — the four larger architectural items, built

The four items the fourth review left as "larger / honestly open" were then built
out (this is the deeper architecture, not another patch layer):

| Item | Built | Mechanism · test |
|---|---|---|
| **Server-authoritative factual rendering** (H1 residual) | ✅ | The model now returns only `empathy_text` (validated to contain NO offer terms or dollar amounts) plus a structured `offer` and `account_facts` **by reference**. The SERVER renders every factual sentence (`_render_reply`, `_offer_sentence`, `_fact_sentence`) from validated data — the customer never reads a model-authored fact, so the "novel prose phrasing" residual is gone · `test_empathy_text_must_be_fact_free`, `test_server_renders_offer_and_facts_not_the_model` |
| **Versioned eval-spec + live/batch parity** (M4) | ✅ | `judge.EVAL_SPEC_VERSION` is a content hash over rubric instructions + schema + pass floor + model + evidence formatter; both `grade_all` and `_grade_and_store` stamp it, and a UNIQUE index enforces one grade per `(conversation, eval-spec)` · `test_eval_spec_version_is_a_content_hash`, `test_one_grade_per_conversation_and_spec` |
| **Durable / idempotent resolution** (M5) | ✅ | `resolve_session` validates without mutating, snapshots the ledger, and rolls back the offer transition if persistence fails (retryable); a second resolve returns the same record; grading runs after the durable commit and a miss is reconciled by `grade_all` · `test_resolve_is_idempotent`, `test_resolve_rolls_back_on_persist_failure` |
| **`run_id` experiment lineage** (M6 full) | ✅ | Conversations carry `run_id` + `phase`; `run_demo` tags baseline/after under one run_id and resets only the cohort's cooldown (no `reset_db`), so baseline conversations, the signal, and after conversations survive as one immutable lineage; metrics are phase-scoped · `test_run_phase_scoped_metrics`, `test_signal_persist_load_carries_run_id` |

Genuinely remaining (smaller, and disclosed): the golden set is still binary-labeled
(no independent per-dimension human scores), and the jailbreak/PII layers remain
pattern-based (100% on the seeded probes, not universal) — the deterministic policy
and server-rendering layers are the real backstops.

## Notes

- Phase 1 onward needs a working `OPENAI_API_KEY` in `.env` (validated 2026-07-17).
- `dashboard/data.js` is committed as the latest demo snapshot so the dashboard renders real numbers on open; re-run `python run_demo.py` to refresh it.
- `scenarios` table is a deliberate deviation from handoff §7 — see README.
- Keep the POC limited-risk (no credit/insurance/health eligibility).
