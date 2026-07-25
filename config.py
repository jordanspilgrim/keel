"""Keel — single source of configuration.

Every model ID, price, policy limit, and threshold lives here so the whole
system is swappable without touching logic (handoff §3, §6). Env vars override
the defaults where noted, so you can retune a demo without editing code.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Model IDs — resolve to the latest available flagship / mini / embedding.
# The plan's economics assume the GPT-5 family (§9). Confirm these against the
# live model list at build time; swap here if newer IDs exist.
# ---------------------------------------------------------------------------
FLAGSHIP_MODEL = os.getenv("KEEL_FLAGSHIP_MODEL", "gpt-5")          # agent reasoning, hard cases
MINI_MODEL = os.getenv("KEEL_MINI_MODEL", "gpt-5-mini")            # LLM-judge, classifiers, triage
EMBEDDING_MODEL = os.getenv("KEEL_EMBEDDING_MODEL", "text-embedding-3-small")

# ---------------------------------------------------------------------------
# Prices — USD per 1M tokens (OpenAI, July 2026). Editable; the *structure* is
# the point, not the exact cents (plan §9). Mirrored in economics.py.
# ---------------------------------------------------------------------------
FLAGSHIP_PRICE_IN = 1.25
FLAGSHIP_PRICE_OUT = 10.0
MINI_PRICE_IN = 0.25
MINI_PRICE_OUT = 2.0
EMBEDDING_PRICE = 0.02  # per 1M tokens

# ---------------------------------------------------------------------------
# Policy / authorization limits (Phase 2 action guardrails, plan §4).
# The model may *propose* past these; the deterministic policy layer caps or
# rejects. These are the numbers a non-engineer would tune — keep them legible.
# ---------------------------------------------------------------------------
MAX_DISCOUNT_PCT = 20           # hard ceiling on any discount offer
MARGIN_FLOOR_USD = 5.0          # never leave the account below this monthly margin
SAVE_OFFER_COOLDOWN_DAYS = 90   # no second save-offer within this window
MAX_PAUSE_MONTHS = 3            # longest pause the agent may grant autonomously

# Tools whose effects are consequential / irreversible → require human approval
# (GDPR Art. 22, plan §5). The agent proposes; a human confirms.
CONSEQUENTIAL_TOOLS = frozenset(
    {"deny_refund", "change_contract_terms", "downgrade_plan", "cancel_subscription"}
)
# Tools safe to run autonomously (reversible, low-stakes) — calibrated autonomy.
AUTONOMOUS_TOOLS = frozenset(
    {"get_customer", "get_subscription", "get_usage", "offer_discount", "offer_pause"}
)

# ---------------------------------------------------------------------------
# Safety thresholds — kill switch drops the agent to safe-mode (disclosure +
# human handoff) if these breach (plan §4 "kill switch & fallback").
# ---------------------------------------------------------------------------
GUARDRAIL_CATCH_RATE_FLOOR = 0.95   # below this on the red-team set → safe-mode
EVAL_PASS_RATE_FLOOR = 0.80         # below this → safe-mode
JUDGE_CONFIDENCE_FLOOR = 0.60       # agent disposition below this → escalate

# Guardrail-health provenance: a persisted red-team result only counts as a valid
# kill-switch input if it was produced by THIS guardrail version and is recent. A
# stale or version-mismatched result must not keep authorizing normal mode after
# the guardrails have changed. The code-identity check is guardrails.guardrail_version(), a
# CONTENT HASH — this string is a human-readable label only and is not load-bearing. It used
# to be the check, and it had already drifted five behavior-changing commits behind the code
# it claimed to version, so a guardrail change that lowered the true catch rate kept
# reporting the stale rate as current and healthy.
GUARDRAIL_VERSION_LABEL = "4"       # prose-bound contract + claim validation + terminal escalation
GUARDRAIL_HEALTH_MAX_AGE_DAYS = 7   # older red-team results are treated as stale

# ---------------------------------------------------------------------------
# Compliance (plan §5). Disclosure is enforced as an input-side guardrail so it
# can never be skipped (EU AI Act Art. 50, binding 2 Aug 2026).
# ---------------------------------------------------------------------------
AI_DISCLOSURE = (
    "Hi — before we begin, I want to be transparent: you're chatting with an AI "
    "assistant, not a human agent. I can help with your subscription and answer "
    "account questions. If anything needs a person, I'll hand you to one."
)
RISK_TIER = "limited"  # retention/CX agent = limited-risk until it touches
# credit/insurance/health eligibility (Annex III), then high-risk (deferred to
# 2 Dec 2027). Keep the POC on the limited-risk side. See README.

# ---------------------------------------------------------------------------
# Synthetic data — seeded for reproducible demo runs (handoff §3, §8).
# ---------------------------------------------------------------------------
SYNTH_SEED = 42
SYNTH_N_CUSTOMERS = 200

# ---------------------------------------------------------------------------
# Analytics (Phase 4).
# ---------------------------------------------------------------------------
CLUSTER_K = 5             # fixed k for KMeans; start simple (handoff §8 open decision)
CLUSTER_RANDOM_STATE = 42
# Relative margin cost of a pause vs. a discount. A discount concedes a % of
# price every month; a pause mostly defers revenue, so its true given-away
# margin is modeled as a small goodwill fraction of price. Tunable.
PAUSE_MARGIN_FRACTION = 0.07
# LOAD-BEARING ASSUMPTION, stated explicitly because the demo's headline conclusion depends
# on it. At 0.07/month a 3-month pause is charged 21% of ONE month's price, so in the
# margin-adjusted north star a 3-month pause (0.79 of a save) and a 20% discount (0.80)
# score almost identically. The DEFENSIBLE reading is that a pause DEFERS revenue rather
# than destroying it — the customer is expected to return at full price, whereas a discount
# is permanent margin loss. That is a modeling choice, not a measurement: if churn-after-
# pause is high, the true cost of a 3-month pause approaches three months of lost revenue
# (300% of one month), which would make pauses far more expensive than discounts and could
# reverse a pause-vs-discount comparison. This POC has no post-pause retention data to
# calibrate it, so the number is an assumption an operator must set from their own cohort
# data — not a result. Any claim of the form "discounts retain this segment better than
# pauses" is conditional on it.

# ---------------------------------------------------------------------------
# Datastore.
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("KEEL_DB_PATH", "keel.db")
