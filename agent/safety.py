"""Program-level safety state — the kill switch (plan §4, config floors).

Derived from the DB: once there's a sample, if the eval pass rate or coverage
breaches the configured floors the program enters SAFE MODE, and new live
sessions disclose + route straight to a human instead of running the agent
autonomously. Surfaced in /api/metrics so the dashboard shows the active state.
"""

from __future__ import annotations

from datetime import datetime, timezone

import config
import db
from agent import guardrails
from evals import judge

_MIN_SAMPLE = 10          # don't judge health until there's a sample
_COVERAGE_FLOOR = 0.90    # evals must cover ≥ this share of conversations


def _health_age_days(created_at: str) -> float | None:
    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def program_state(conn) -> dict:
    """Return {mode, healthy, reasons, advisories, metrics}. healthy=False → safe mode.

    The red-team guardrail catch rate is an OFFLINE metric (an LLM scope call per
    probe), so we read the value the last sweep persisted rather than recompute it.
    A result only GATES if it was produced by the current guardrail version and is
    recent: a stale or version-mismatched result must not keep authorizing normal
    mode after guardrails changed, and a below-floor result forces safe mode. A
    result that has never been recorded is surfaced as a non-gating advisory (the
    console is usable out of the box; the 'not validated' state is visible)."""
    total = conn.execute("SELECT count(*) FROM conversations").fetchone()[0]
    reasons: list[str] = []
    advisories: list[str] = []
    metrics: dict = {"conversations": total}
    if total >= _MIN_SAMPLE:
        # count ONLY current-spec grades, one per conversation (see judge helper) — the
        # kill switch must never read a >100% pass rate off a multi-version history.
        passes, graded = judge.current_spec_eval_counts(conn)
        pass_rate = passes / total
        coverage = graded / total
        metrics.update({"eval_pass_rate": round(pass_rate, 3), "eval_coverage": round(coverage, 3)})
        if pass_rate < config.EVAL_PASS_RATE_FLOOR:
            reasons.append(f"eval pass rate {pass_rate:.0%} below floor {config.EVAL_PASS_RATE_FLOOR:.0%}")
        if coverage < _COVERAGE_FLOOR:
            reasons.append(f"eval coverage {coverage:.0%} below {_COVERAGE_FLOOR:.0%}")

    row = db.latest_health_row(conn, "guardrail_catch_rate")
    if row is None:
        metrics["guardrail_catch_rate"] = None
        advisories.append("guardrail red-team never run — catch rate not validated")
    else:
        catch_rate = float(row["value"])
        age = _health_age_days(row["created_at"])
        metrics["guardrail_catch_rate"] = round(catch_rate, 3)
        metrics["guardrail_health_version"] = row["version"]
        metrics["guardrail_health_age_days"] = round(age, 2) if age is not None else None
        if row["version"] != guardrails.guardrail_version():
            reasons.append(f"guardrail health from version {row['version']} != current "
                           f"{guardrails.guardrail_version()} (re-run the red-team)")
        elif age is not None and age > config.GUARDRAIL_HEALTH_MAX_AGE_DAYS:
            reasons.append(f"guardrail health is {age:.0f}d old (> {config.GUARDRAIL_HEALTH_MAX_AGE_DAYS}d)")
        elif catch_rate < config.GUARDRAIL_CATCH_RATE_FLOOR:
            reasons.append(
                f"guardrail catch rate {catch_rate:.0%} below floor {config.GUARDRAIL_CATCH_RATE_FLOOR:.0%}")
    healthy = not reasons
    return {"mode": "normal" if healthy else "safe", "healthy": healthy,
            "reasons": reasons, "advisories": advisories, "metrics": metrics}
